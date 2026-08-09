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
        prompt = cfg.get("prompt") or ", ".join(
            p for p in (subject, hint, style) if p)
        lcm = bool(opt(cfg, "lcm", False))

        # A reference may shape the anchor itself, not just the frames derived
        # from it. Without this the canonical is generated from the prompt
        # alone, so "here is my character, build a sheet of it" cannot work.
        lib = ctx.references()
        from_ref = opt(cfg, "from_reference", {}) or {}
        want_view = resolve_view(opt(cfg, "view", ctx.stage_config("pose").get("view", "side")))

        chosen = None
        if lib.identity and opt(from_ref, "enabled", True):
            chosen, _, dist = refs_mod.pick(lib.identity, want_view, tolerance=180.0)
            print(f"   identity from {chosen.label} ({dist:.0f}deg away)")

        g = comfy.Graph()
        model, pos, neg, vae = comfy.base_graph(
            g,
            prompt=prompt,
            negative=opt(cfg, "negative", comfy.NEGATIVE),
            lora_strength=opt(cfg, "lora_strength", 1.2),
            lcm=lcm,
            models=ctx.config.get("models") or {},
        )

        latent = None
        denoise = 1.0
        if chosen is not None:
            ref_name = client.upload_image(chosen.path)
            ref_img = g.out(g.add("LoadImage", image=ref_name), 0)
            model = comfy.apply_ipadapter(
                g, model, ref_img,
                weight=float(opt(from_ref, "weight", chosen.base_weight)),
                weight_type=opt(from_ref, "weight_type", "style and composition"),
                start_at=0.0, end_at=1.0,
            )

        # Style exemplars ride on top at a much lower weight: they say how the
        # art should look, not who the character is.
        for exemplar in lib.style[:2]:
            name = client.upload_image(exemplar.path)
            model = comfy.apply_ipadapter(
                g, model, g.out(g.add("LoadImage", image=name), 0),
                weight=refs_mod.style_weight([exemplar], opt(cfg, "style_weight", None)),
                weight_type="style transfer",
                start_at=0.0, end_at=0.8,
            )
        if lib.style:
            print(f"   style from {len(lib.style[:2])} exemplar(s)")
            # Deliberately no img2img from an identity reference. Those are
            # usually illustrations, and denoising from one traces its
            # rendering — gradients and soft edges — which is the opposite of
            # a sprite. The pixelation has to come from generation.
            denoise = 1.0

        batch = max(1, int(opt(cfg, "candidates", 1)))
        comfy.sample_and_save(
            g, model, pos, neg, vae,
            width=opt(cfg, "width", 1024), height=opt(cfg, "height", 1024),
            batch=batch,
            seed=opt(cfg, "seed", 1234),
            steps=opt(cfg, "steps", 8 if lcm else 25),
            cfg=opt(cfg, "cfg", 1.5 if lcm else 7.0),
            lcm=lcm,
            denoise=denoise,
            latent=latent,
            sampler=opt(cfg, "sampler", None),
            scheduler=opt(cfg, "scheduler", None),
            prefix=f"{ctx.run_id}_canonical",
        )

        images = client.generate(g.build(), timeout=opt(cfg, "timeout", 1800))
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
