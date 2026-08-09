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
from .. import cooling
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
    optional = frozenset({"skeletons", "depthmaps", "pose_frames"})
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

        # The anchor gets the same structural conditioning a frame gets.
        #
        # It did not, and that was the largest defect in the pipeline. The pose
        # and depth stages run first and their output was sitting there unused:
        # the canonical was generated from prompt and IP-Adapter alone, with no
        # ControlNet, no depth map and no prop geometry. Three symptoms came
        # out of that one gap.
        #
        #   Two bows.        Props reached the canonical as words with no
        #                    volume, which is exactly the failure props.py
        #                    warns about - told only "holding a bow", the model
        #                    decides for itself where it goes, and sometimes
        #                    decides twice.
        #   Broken anatomy.  Nothing said where the limbs were.
        #   Worse the more   An identity reference in a dynamic pose disagrees
        #   dynamic the      with a standing prompt, and with no ControlNet
        #   reference.       nothing arbitrates. The disagreement scales with
        #                    the pose difference, so a running archer degrades
        #                    where a standing knight does not.
        #
        # The anchor is conceptually the first frame, so it is conditioned like
        # one. Optional rather than required: a run that skips the pose stage
        # still works, it just gets the old unconditioned behaviour.
        skeletons = ctx.artifacts.get("skeletons") or []
        depthmaps = ctx.artifacts.get("depthmaps") or []
        guide = skeletons[0] if skeletons else None
        depth = depthmaps[0] if depthmaps else None
        cn = opt(cfg, "controlnet", {})
        use_control = bool(opt(cn, "enabled", True)) and (guide or depth)

        control_names: dict = {}
        if use_control:
            channel = opt(cn, "union_type", None) or ctx.rig().skeleton_control
            if guide and channel:
                control_names["pose"] = (client.upload_image(guide), channel)
            if depth:
                control_names["depth"] = (client.upload_image(depth), "depth")
            # Say WHERE the geometry came from, not just that there was some.
            # A skeleton synthesised from the rig and a skeleton traced off an
            # annotated drawing are different claims about the output, and the
            # log is the only place that distinction survives the run.
            entries = ctx.artifacts.get("pose_frames") or []
            origin = (entries[0] or {}).get("from_annotation") if entries else None
            source = ctx.stage_config("pose").get("source", "library")
            where = (f"annotation of {Path(origin).name}" if origin
                     else f"{source} pose, {ctx.rig().label}")
            print(f"   conditioned by {', '.join(control_names) or 'nothing'}"
                  f"  <- {where}")

        # Candidates go out as one batch by default, and the reason is a
        # correction rather than a design.
        #
        # A four-candidate run once died on a 1800 s timeout, and that was read
        # here as the working set exceeding 16 GB and macOS swapping — the same
        # story as --gpu-only. Sequential generation was written to fix it. Then
        # the two paths were actually measured against each other, four
        # candidates at 1024 each:
        #
        #     sequential   2096 s   2,809,129 swapins
        #     batch        1806 s   1,281,996 swapins
        #
        # Batch is 14% faster and swaps *less than half* as much. The original
        # failure was not a swap catastrophe at all: batch takes 1806 s and the
        # timeout was 1800 s. It missed by six seconds.
        #
        # Sequential is kept, and is not merely a curiosity — it is the only
        # path that can rest between candidates, so a queue that runs all night
        # for thermal reasons wants it despite being slower. That is a real
        # trade and it belongs to whoever is running the machine.
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
                    comfy.BACKDROP_NEGATIVE if backdrop else "",
                    # Naming the failure is what stops the guide being drawn.
                    comfy.POSE_NEGATIVE if control_names.get("pose") else "") if p),
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
            for kind, (name, channel) in control_names.items():
                control = g.out(g.add("LoadImage", image=name), 0)
                strong = kind == "pose"
                pos, neg = comfy.apply_controlnet(
                    g, pos, neg, control, vae,
                    # Weaker than the frames stage, and the reason is
                    # structural rather than aesthetic. A frame is pulled
                    # against its ControlNet by two image conditionings - the
                    # canonical anchor at 0.9 and a matched identity reference
                    # at 0.85. The canonical has only the reference: it IS the
                    # anchor, so it cannot have one. Half the counterweight
                    # meeting the same strength is not the same experiment,
                    # and copying the frames figures here was an oversight.
                    strength=opt(cn, "strength", 0.55 if strong else 0.30),
                    start_percent=opt(cn, "start_percent", 0.0),
                    # Released earlier too. The anchor's job is to be a clean
                    # readable character, not to reproduce a specific pose to
                    # the pixel; the frames are where pose fidelity is paid for.
                    end_percent=opt(cn, "end_percent", 0.40 if strong else 0.35),
                    union_type=channel,
                    controlnet=(ctx.config.get("models") or {}).get("controlnet"),
                )
            return g, model, pos, neg, vae

        if lib.style:
            print(f"   style from {len(lib.style[:2])} exemplar(s)")

        wanted = max(1, int(opt(cfg, "candidates", 1)))
        base_seed = opt(cfg, "seed", 1234)
        timeout = opt(cfg, "timeout", 1800)
        images: list[bytes] = []

        if bool(opt(cfg, "batch_candidates", True)) and wanted > 1:
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
                cooling.rest(ctx.config, after=f"candidate {n + 1}",
                             last=n == wanted - 1)

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
