"""Stage 2 — the canonical sprite: one image that defines who the character is.

Everything downstream refers back to this. It is generated once, at the highest
quality settings in the config, because every animation frame inherits its
identity through IP-Adapter and its colours through the extracted palette. A
weak canonical propagates into every frame.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import comfy
from .. import rigs as rig_lib
from ..bodyspace import resolve_view
from .. import references as refs_mod
from ..stage import Context, Resource, Stage, opt, register


def _anchor_view(ctx, cfg) -> str | float:
    """Which way the anchor faces.

    Three sources, most specific first, and the last one is the fix for a real
    failure. A character sheet lists its views in `pose.set` and never sets
    `pose.view`, so this used to fall through to the literal default "side" —
    the anchor was rendered in profile for a sheet whose first view is front,
    and a reference labelled `front` was then measured as 90 degrees away and
    down-weighted by the falloff for being a poor match. It was a poor match
    only because the target had been chosen wrongly.

    The anchor should face the same way as the first frame that will inherit
    from it. That is the frame it has the best chance of matching, and every
    other frame turns away from it symmetrically.
    """
    explicit = opt(cfg, "view", None)
    if explicit is not None:
        return explicit

    pose_cfg = ctx.stage_config("pose")
    named = opt(pose_cfg, "view", None)
    if named is not None:
        return named

    first = (opt(pose_cfg, "set", []) or [{}])[0]
    return first.get("view", "side") if isinstance(first, dict) else "side"


@register
class CanonicalStage(Stage):
    name = "canonical"
    resource = Resource.GPU
    produces = frozenset({"canonical"})

    def run(self, ctx: Context) -> dict[str, Any]:
        cfg = ctx.stage_config("canonical")
        subject = ctx.config.get("subject", "a knight in armor")
        client = comfy.Client(ctx.config.get("comfy", {}).get("host", "http://127.0.0.1:8188"))
        if not client.alive():
            raise RuntimeError("ComfyUI is not running — start it with ./start.sh")

        default_style = "pixel art, game sprite, side view, plain flat background"
        style = ctx.config.get("style", default_style)
        hint = ctx.rig().prompt_hint
        bg = ctx.config.get("background") or {}
        backdrop = None if opt(bg, "enabled", True) is False else opt(
            bg, "colour", comfy.BACKDROP)
        prompt = cfg.get("prompt") or ", ".join(
            p for p in (subject, hint, style,
                        comfy.backdrop_prompt(backdrop) if backdrop else "") if p)
        lcm = bool(opt(cfg, "lcm", False))

        # A reference may shape the anchor itself, not just the frames derived
        # from it. Without this the canonical is generated from the prompt
        # alone, so "here is my character, build a sheet of it" cannot work.
        lib = ctx.references()
        from_ref = opt(cfg, "from_reference", {}) or {}
        want_view = resolve_view(_anchor_view(ctx, cfg))

        chosen = None
        if lib.identity and opt(from_ref, "enabled", True):
            chosen, _, dist = refs_mod.pick(lib.identity, want_view, tolerance=180.0)
            print(f"   identity from {chosen.label} ({dist:.0f}deg away)")

        # Candidates run one at a time, not as a batch.
        #
        # A batch of four at 1024 does not cost four times one image on 16 GB
        # of unified memory — it blew through a 1800 s timeout where a single
        # image takes 280 s, because the working set stops fitting and macOS
        # swaps. Same failure as --gpu-only, which measured 4.9x slower for the
        # same reason. Sequential keeps peak memory flat and reports progress.
        #
        # The graph is rebuilt per candidate rather than resampled, because a
        # ComfyUI graph carries its seed inside a node: reusing one would need
        # the node mutated in place, and building it again costs nothing.
        uploads: dict = {}

        def build():
            g = comfy.Graph()
            model, pos, neg, vae = comfy.base_graph(
                g,
                prompt=prompt,
                negative=", ".join(p for p in (
                    opt(cfg, "negative", comfy.NEGATIVE),
                    comfy.BACKDROP_NEGATIVE if backdrop else "") if p),
                lora_strength=opt(cfg, "lora_strength", 1.2),
                lcm=lcm,
                models=ctx.config.get("models") or {},
            )

            # No img2img from an identity reference, deliberately. Those are
            # usually illustrations, and denoising from one traces its
            # rendering — gradients, soft edges, anti-aliasing — which is the
            # opposite of a sprite. Identity goes through IP-Adapter only and
            # the pixelation comes from generation.
            #
            # comfy.encode_image is kept for the illustrate → pixelise pass,
            # which is a different thing: there the source is already in the
            # target style and tracing it is the point.
            if chosen is not None:
                if chosen.path not in uploads:
                    uploads[chosen.path] = client.upload_image(chosen.path)
                ref_img = g.out(g.add("LoadImage", image=uploads[chosen.path]), 0)
                model = comfy.apply_ipadapter(
                    g, model, ref_img,
                    weight=float(opt(from_ref, "weight", chosen.base_weight)),
                    weight_type=opt(from_ref, "weight_type", "linear"),
                    start_at=0.0, end_at=1.0,
                    ipadapter=(ctx.config.get("models") or {}).get("ipadapter"),
                )

            # Style exemplars ride on top at a much lower weight: they say how
            # the art should look, not who the character is.
            for exemplar in lib.style[:2]:
                if exemplar.path not in uploads:
                    uploads[exemplar.path] = client.upload_image(exemplar.path)
                model = comfy.apply_ipadapter(
                    g, model, g.out(g.add("LoadImage", image=uploads[exemplar.path]), 0),
                    weight=refs_mod.style_weight(
                        [exemplar], opt(cfg, "style_weight", None)),
                    weight_type="style transfer",
                    start_at=0.0, end_at=0.8,
                    ipadapter=(ctx.config.get("models") or {}).get("ipadapter"),
                )
            return g, model, pos, neg, vae

        if lib.style:
            print(f"   style from {len(lib.style[:2])} exemplar(s)")

        wanted = max(1, int(opt(cfg, "candidates", 1)))
        base_seed = opt(cfg, "seed", 1234)
        timeout = opt(cfg, "timeout", 1800)
        images: list[bytes] = []

        # `batch_candidates` exists to be measured against, not because it is
        # recommended. Keeping the losing path runnable is the only way the
        # claim stays checkable on a different machine, where a card with real
        # VRAM would likely reverse it.
        if bool(opt(cfg, "batch_candidates", False)) and wanted > 1:
            g, model, pos, neg, vae = build()
            comfy.sample_and_save(
                g, model, pos, neg, vae,
                width=opt(cfg, "width", 1024), height=opt(cfg, "height", 1024),
                batch=wanted,
                seed=base_seed,
                steps=opt(cfg, "steps", 8 if lcm else 25),
                cfg=opt(cfg, "cfg", 1.5 if lcm else 7.0),
                lcm=lcm,
                denoise=1.0,
                sampler=opt(cfg, "sampler", None),
                scheduler=opt(cfg, "scheduler", None),
                prefix=f"{ctx.run_id}_canonical",
            )
            print(f"   {wanted} candidates as one batch")
            images = client.generate(g.build(), timeout=timeout)
            wanted = 0                       # the loop below has nothing to do

        for n in range(wanted):
            g, model, pos, neg, vae = build()
            comfy.sample_and_save(
                g, model, pos, neg, vae,
                width=opt(cfg, "width", 1024), height=opt(cfg, "height", 1024),
                batch=1,
                seed=base_seed + n,
                steps=opt(cfg, "steps", 8 if lcm else 25),
                cfg=opt(cfg, "cfg", 1.5 if lcm else 7.0),
                lcm=lcm,
                denoise=1.0,
                sampler=opt(cfg, "sampler", None),
                scheduler=opt(cfg, "scheduler", None),
                prefix=f"{ctx.run_id}_canonical{n:02d}",
            )
            images += client.generate(g.build(), timeout=timeout)
            if wanted > 1:
                print(f"   candidate {n + 1}/{wanted} (seed {base_seed + n})")

        outdir = ctx.stage_dir("canonical")
        dst: Path = outdir / "canonical.png"
        dst.write_bytes(images[0])
        # Extra candidates are kept beside the chosen one so a weak anchor can
        # be swapped without paying for the batch again.
        for i, blob in enumerate(images[1:], start=1):
            (outdir / f"candidate_{i:02d}.png").write_bytes(blob)
        if len(images) > 1:
            print(f"   {len(images)} candidates kept; canonical.png is the first")
        print(f"   canonical -> {dst.relative_to(ctx.root)}  (seed {opt(cfg, 'seed', 1234)})")
        return {"canonical": dst}
