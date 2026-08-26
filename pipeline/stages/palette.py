"""Stage 4 — pixelize every frame against ONE shared palette."""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Mapping, Any

import numpy as np
from PIL import Image

from ..definitive.pixelize import (background_to_alpha, extract_palette, find_phase,
                        load_palette, pixelize, reduce_blocks, save_palette)
from ..generation.stage import Context, Resource, Stage, opt, register
from ..shared.errors import Invalid, NotFound


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

    def prepare(self, ctx: Context) -> dict[str, Any]:
        cfg = ctx.stage_config("palette")
        canonical: Path = ctx.require("canonical")
        key_colour = self._key_colour(ctx)

        # Frames of a run share a grid — same model, same size — so searching per frame paid N times for one answer: measured 6/6 frames identical.
        arr = np.asarray(Image.open(canonical).convert("RGB"))
        phase = find_phase(arr, cfg["factor"])
        print(f"   grid phase {phase}, shared by every frame")

        source = cfg["source"]
        if source == "file":
            ref = cfg["file"]
            if not ref:
                raise Invalid(
                    "palette.source is 'file' but palette.file is not set. "
                    "Pick a palette, or use source: extract.",
                    field="file",
                )
            given = self._resolve_file(ctx, ref)
            palette = load_palette(given)
            print(f"   palette from {given.relative_to(ctx.root)} ({len(palette)} colours)")
        elif source == "llm":
            palette = self._choose(ctx, cfg)
        else:
            palette = self._extract_from_subject(
                arr, phase, cfg["size"], cfg["factor"], cfg["reduce"],
                cfg["alpha_tolerance"], key_colour, float(cfg["clip_tolerance"]))
            print(f"   extracted {len(palette)} colours from the canonical's subject")

        return {"palette": palette, "phase": phase, "key_colour": key_colour}

    @staticmethod
    def _key_colour(ctx: Context) -> tuple[int, int, int] | None:
        bg = ctx.config.get("background") or {}
        if opt(bg, "enabled", True) is False:
            return None
        raw = str(opt(bg, "colour", "") or "").lstrip("#")
        if len(raw) != 6:
            return None
        return tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4))

    def run(self, ctx: Context, prep: Mapping[str, Any]) -> dict[str, Any]:
        cfg = ctx.stage_config("palette")
        frames: list[Path] = ctx.artifacts.get("soft_frames") or ctx.require("frames")
        outdir = ctx.stage_dir("palette")

        pal_path = outdir / "palette.hex"
        palette = prep["palette"]
        save_palette(palette, pal_path, note=f"run {ctx.run_id}")

        # Defaults chosen by measurement, not by taste: median beat mode 100% to 70% on structural accuracy, because anti-aliased input makes almost every pixel [...]
        jobs = [
            (
                str(src), str(outdir / f"{src.stem}_px.png"),
                cfg["factor"], cfg["reduce"], palette, cfg["alpha_tolerance"],
                cfg["upscale"], bool(cfg["dither"]), prep["phase"],
                str(cfg["match"]), prep["key_colour"], float(cfg["clip_tolerance"]),
            )
            for src in frames
        ]
        compute = ctx.config.get("compute") or {}
        wanted = opt(compute, "cpu_workers", opt(cfg, "workers", os.cpu_count() or 4))
        workers = max(1, min(int(wanted), len(jobs)))
        if workers > 1:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                done = list(pool.map(_one_frame, jobs))
        else:
            done = [_one_frame(j) for j in jobs]

        print(f"   pixelized {len(done)} frame(s) across {workers} worker(s)")
        return {"palette": pal_path, "pixel_frames": [Path(p) for p in done]}

    @staticmethod
    def _extract_from_subject(
        arr, phase: tuple[int, int], size: int, factor: int, reduce: str,
        alpha_tol: int, key_colour: tuple[int, int, int] | None = None,
        tolerance: float = 32.0,
    ) -> list[tuple[int, int, int]]:
        """A sprite occupies roughly 15% of the canvas, so extracting from the whole image spends almost the entire budget subdividing one [...]"""
        ox, oy = phase
        small = reduce_blocks(arr, factor, ox, oy, reduce, tolerance)
        keyed = background_to_alpha(small, alpha_tol, key=key_colour)
        rgb, alpha = keyed[..., :3], keyed[..., 3]

        opaque = int((alpha > 0).sum())
        if opaque < size * 4:
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
        raise NotFound("palette", ref, available=list(found))

    @staticmethod
    def _choose(ctx: Context, cfg: dict):
        from ..refs.llm import LLMError, Ollama
        from ..looks.palettes import choose

        llm_cfg = {**(ctx.stage_config("pose").get("llm") or {}), **(cfg.get("llm") or {})}
        client = Ollama(
            host=opt(llm_cfg, "host", "http://127.0.0.1:11434"),
            model=opt(llm_cfg, "model", "qwen3:4b"),
            keep_alive=opt(llm_cfg, "keep_alive", 0),
        )
        if not client.alive():
            raise LLMError(
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
