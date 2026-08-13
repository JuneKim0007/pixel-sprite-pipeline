"""Stage 4 — pixelize every frame against ONE shared palette.

This is where colour consistency is actually won, and it is deliberately not
an AI step. The palette is extracted once from the canonical sprite and then
imposed on every frame, so the animation cannot drift in colour: the frames
are quantised to an identical, fixed set.

Genuinely parallel work, so it runs in a process pool — each frame is
independent, and the phase search plus flood fill are CPU-bound Python.
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..definitive.pixelize import (background_to_alpha, extract_palette, find_phase,
                        load_palette, pixelize, reduce_blocks, save_palette)
from ..generation.stage import Context, Resource, Stage, opt, register


def _one_frame(args: tuple) -> str:
    """Pool worker. Arguments stay primitive so they pickle cheaply."""
    src, dst, factor, reduce, palette, alpha_tol, upscale, dither, phase, match, key, tolerance = args
    pixelize(
        Path(src), Path(dst), factor, reduce,
        colours=0, palette=palette, dither=dither, match=match, key=key, tolerance=tolerance,
        alpha_tol=alpha_tol, upscale=upscale, phase=phase, verbose=False,
    )
    return dst


@register
class PaletteStage(Stage):
    name = "palette"
    resource = Resource.CPU
    requires = frozenset({"frames", "canonical"})
    optional = frozenset({"soft_frames"})
    produces = frozenset({"palette", "pixel_frames"})

    def run(self, ctx: Context) -> dict[str, Any]:
        cfg = ctx.stage_config("palette")
        canonical: Path = ctx.require("canonical")
        # Prefer warped frames when the softbody stage ran, so secondary motion
        # survives into the pixelized output.
        frames: list[Path] = ctx.artifacts.get("soft_frames") or ctx.require("frames")
        outdir = ctx.stage_dir("palette")

        size = opt(cfg, "size", 12)
        factor = opt(cfg, "factor", 8)
        reduce = opt(cfg, "reduce", "median")
        alpha_tol = opt(cfg, "alpha_tolerance", 14)
        upscale = opt(cfg, "upscale", 1)
        pal_path = outdir / "palette.hex"

        # If the prompt named the backdrop, the keyer knows what to remove
        # instead of sampling a corner and hoping the corner was background.
        bg = ctx.config.get("background") or {}
        key_colour = None
        if opt(bg, "enabled", True) is not False:
            raw = str(opt(bg, "colour", "") or "").lstrip("#")
            if len(raw) == 6:
                key_colour = tuple(int(raw[i:i+2], 16) for i in (0, 2, 4))


        # Either reuse a palette you already committed to, or derive one from
        # the canonical sprite. Reuse is what keeps separate runs of the same
        # character on-model.
        source = opt(cfg, "source", "extract")
        if source == "file":
            ref = opt(cfg, "file", "")
            if not ref:
                raise ValueError(
                    "palette.source is 'file' but palette.file is not set. "
                    "Pick a palette, or use source: extract."
                )
            given = self._resolve_file(ctx, ref)
            palette = load_palette(given)
            print(f"   palette from {given.relative_to(ctx.root)} ({len(palette)} colours)")
        elif source == "llm":
            palette = self._choose(ctx, cfg)
        else:
            palette = self._extract_from_subject(
                canonical, size, factor, reduce, alpha_tol, key_colour,
                float(opt(cfg, "clip_tolerance", 32.0)))
            print(f"   extracted {len(palette)} colours from the canonical's subject")
        save_palette(palette, pal_path, note=f"run {ctx.run_id}")

        # Defaults chosen by measurement, not by taste: median beat mode 100%
        # to 70% on structural accuracy, because anti-aliased input makes
        # almost every pixel unique and the most frequent one arbitrary.
        dither = bool(opt(cfg, "dither", False))
        # How "nearest colour" is decided. Only matters when a palette is
        # imposed rather than extracted — an extracted palette already came
        # from these pixels, so every metric agrees.
        match = str(opt(cfg, "match", "weighted"))
        phase_mode = opt(cfg, "phase", "per_frame")
        shared_phase = None
        if phase_mode == "locked":
            arr = np.asarray(Image.open(canonical).convert("RGB"))
            shared_phase = find_phase(arr, factor)
            print(f"   grid phase locked to the canonical at {shared_phase}")

        # Only the clipped reducer reads this; harmless for the others.
        tolerance = float(opt(cfg, "clip_tolerance", 32.0))

        jobs = [
            (
                str(src), str(outdir / f"{src.stem}_px.png"),
                factor, reduce, palette, alpha_tol, upscale,
                dither, shared_phase, match, key_colour, tolerance,
            )
            for src in frames
        ]
        workers = min(opt(cfg, "workers", os.cpu_count() or 4), len(jobs)) or 1
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                done = list(pool.map(_one_frame, jobs))
        else:
            done = [_one_frame(j) for j in jobs]

        print(f"   pixelized {len(done)} frame(s) across {workers} worker(s)")
        return {"palette": pal_path, "pixel_frames": [Path(p) for p in done]}

    @staticmethod
    def _extract_from_subject(
        canonical: Path, size: int, factor: int, reduce: str, alpha_tol: int,
        key_colour: tuple[int, int, int] | None = None,
        tolerance: float = 32.0,
    ) -> list[tuple[int, int, int]]:
        """Derive the palette from the character, ignoring the backdrop.

        Median cut allocates colours in proportion to how many pixels want
        them. A sprite occupies roughly 15% of the canvas, so extracting from
        the whole image spends almost the entire budget subdividing one flat
        background grey and returns a palette of near-identical greys — the
        character's actual colours never make it in.

        So the background is keyed out first and only opaque pixels are
        sampled. The palette then describes the subject, which is the only
        part that gets drawn.
        """
        arr = np.asarray(Image.open(canonical).convert("RGB"))
        ox, oy = find_phase(arr, factor)
        small = reduce_blocks(arr, factor, ox, oy, reduce, tolerance)
        keyed = background_to_alpha(small, alpha_tol, key=key_colour)
        rgb, alpha = keyed[..., :3], keyed[..., 3]

        opaque = int((alpha > 0).sum())
        if opaque < size * 4:
            # Almost everything was treated as background — the sprite may run
            # to the canvas edge. Fall back to the whole image rather than
            # extracting from a handful of pixels.
            return extract_palette(arr, size)
        return extract_palette(rgb, size, ignore_alpha=alpha)

    @staticmethod
    def _resolve_file(ctx: Context, ref: str) -> Path:
        """Accept a registry key ('character/dungeon_steel') or a plain path."""
        from ..looks.palettes import discover

        found = discover(ctx.root)
        if ref in found:
            return found[ref].path
        direct = ctx.root / ref
        if direct.exists():
            return direct
        raise FileNotFoundError(
            f"no palette '{ref}'. Known keys: {', '.join(sorted(found)) or '(none)'}"
        )

    @staticmethod
    def _choose(ctx: Context, cfg: dict):
        """Let the LLM pick from the registry, then verify the pick exists.

        Selection only — application below stays deterministic. That split is
        the point: an LLM is a reasonable judge of which palette suits a
        subject, and a terrible mechanism for applying one consistently.
        """
        from ..refs.llm import Ollama
        from ..looks.palettes import choose

        llm_cfg = {**(ctx.stage_config("pose").get("llm") or {}), **(cfg.get("llm") or {})}
        client = Ollama(
            host=opt(llm_cfg, "host", "http://127.0.0.1:11434"),
            model=opt(llm_cfg, "model", "qwen3:4b"),
            keep_alive=opt(llm_cfg, "keep_alive", 0),
        )
        if not client.alive():
            raise RuntimeError(
                "palette.source is 'llm' but Ollama is not running. Start it with "
                "`ollama serve`, or use source: extract."
            )
        pal = choose(
            ctx.root,
            subject=ctx.config.get("subject", ""),
            style=ctx.config.get("style", ""),
            client=client,
            temperature=opt(llm_cfg, "temperature", 0.4),
            attempts=opt(llm_cfg, "attempts", 3),
        )
        return pal.colours
