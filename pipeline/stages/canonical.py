"""Stage 2 — the canonical sprite: one image that defines who the character is.

Everything downstream refers back to this. It is generated once, at the highest
quality settings in the config, because every animation frame inherits its
identity through IP-Adapter and its colours through the extracted palette. A
weak canonical propagates into every frame.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..generation import comfy
from ..orchestration import cooling
from ..geometry import rigs as rig_lib
from ..geometry.bodyspace import resolve_view
from ..refs import references as refs_mod
from ..generation.stage import Context, Resource, Stage, opt, register


def _label_for(yaw: float) -> str:
    """Filename-safe name for a view, so a canonical can be found by name.

    Named views only span 0-180, so the character's right side has no name and
    becomes its angle. That is deliberate: `side` already means 90, and giving
    270 a name that reads like a mirror of it is how the two get swapped.
    """
    from ..geometry.bodyspace import VIEWS
    for name, deg in VIEWS.items():
        if abs(deg - (round(yaw) % 360)) < 0.5:
            return name
    return f"{round(yaw) % 360:03d}"


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
    produces = frozenset({"canonical", "canonicals"})

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

        def _prepare(view: float) -> None:
            """Point the closure state at `view`, so build() renders that angle.

            build() reads want_view / chosen / control_names at CALL time, so
            rebinding them here is what makes one graph builder serve every
            view without duplicating it.
            """
            nonlocal want_view, chosen, guide, depth, control_names
            want_view = view
            chosen = None
            if lib.identity and opt(from_ref, "enabled", True):
                chosen, _, d = refs_mod.pick(lib.identity, view, tolerance=180.0)
                print(f"   identity from {chosen.label} ({d:.0f}deg away)")
            i = 0
            ents = ctx.artifacts.get("pose_frames") or []
            if ents:
                i = min(range(len(ents)), key=lambda k: refs_mod.angular_distance(
                    float((ents[k] or {}).get("yaw", 0.0)), view))
            guide = skeletons[i] if i < len(skeletons) else (skeletons[0] if skeletons else None)
            depth = depthmaps[i] if i < len(depthmaps) else (depthmaps[0] if depthmaps else None)
            control_names = {}
            if bool(opt(cn, "enabled", True)) and (guide or depth):
                ch = opt(cn, "union_type", None) or ctx.rig().skeleton_control
                if guide and ch:
                    control_names["pose"] = (client.upload_image(guide), ch)
                if depth:
                    control_names["depth"] = (client.upload_image(depth), "depth")

        chosen = None
        if lib.identity and opt(from_ref, "enabled", True):
            chosen, _, dist = refs_mod.pick(lib.identity, want_view, tolerance=180.0)
            print(f"   identity from {chosen.label} ({dist:.0f}deg away)")

        # The anchor gets the same structural conditioning a frame gets.
        #
        # Without it the canonical came from prompt and IP-Adapter alone - no
        # ControlNet, no depth, no prop geometry - while the pose and depth
        # stages' output sat unused. Three symptoms came from that one gap:
        # duplicate props (told only "holding a bow", the model decides where
        # it goes and sometimes decides twice), broken anatomy, and degradation
        # proportional to how dynamic the reference pose was.
        skeletons = ctx.artifacts.get("skeletons") or []
        depthmaps = ctx.artifacts.get("depthmaps") or []

        # Condition on the geometry for the view being rendered, not index 0.
        # `canonical.view` already chose the REFERENCE by angle, but the guide
        # and depth map were pinned to the first pose frame - so asking for a
        # side anchor gave a side reference conditioned on front geometry, and
        # the two pulled against each other with nothing in the log to say so.
        _entries = ctx.artifacts.get("pose_frames") or []
        _idx = 0
        if _entries:
            _idx = min(
                range(len(_entries)),
                key=lambda i: refs_mod.angular_distance(
                    float((_entries[i] or {}).get("yaw", 0.0)), want_view),
            )
        guide = skeletons[_idx] if _idx < len(skeletons) else (skeletons[0] if skeletons else None)
        depth = depthmaps[_idx] if _idx < len(depthmaps) else (depthmaps[0] if depthmaps else None)
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

        # Candidates go out as one batch by default. Measured: batch 1806 s and
        # 1.28M swap-ins against sequential 2096 s and 2.8M, and candidate 0 is
        # byte-identical either way - a diffusion UNet uses GroupNorm, so
        # nothing crosses the batch dimension.
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
        # Weaker than the frames stage: the anchor has no anchor of its own, so
        # strong control here fights the identity reference instead of guiding
        # it.
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

        base_seed = opt(cfg, "seed", 1234)
        timeout = opt(cfg, "timeout", 1800)

    # One anchor per view, or the single front anchor as a fallback. A rear
    # frame conditioned on a front anchor inherits the front's silhouette,
    # which is what makes a back view come out with a face on it.
        _entries_all = ctx.artifacts.get("pose_frames") or []
        if bool(opt(cfg, "per_view", False)) and _entries_all:
            views = [float((e or {}).get("yaw", 0.0)) for e in _entries_all]
        else:
            views = [want_view]

        outdir = ctx.stage_dir("canonical")
        made: dict[float, Path] = {}
        primary: Path | None = None

        for vi, view in enumerate(views):
            if len(views) > 1:
                print(f"   -- anchor {vi + 1}/{len(views)} at {view:g}deg")
            _prepare(view)
            wanted = max(1, int(opt(cfg, "candidates", 1)))
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

            label = _label_for(view)
            dst = outdir / (f"canonical_{label}.png" if len(views) > 1 else "canonical.png")
            dst.write_bytes(images[0])
            for i, blob in enumerate(images[1:], start=1):
                (outdir / f"candidate_{label}_{i:02d}.png").write_bytes(blob)
            made[round(view) % 360] = dst
            if primary is None:
                primary = dst
            print(f"   canonical -> {dst.relative_to(ctx.root)}  (seed {base_seed})")
            if len(views) > 1:
                cooling.rest(ctx.config, after=f"anchor {vi + 1}",
                             last=vi == len(views) - 1)

        return {"canonical": primary, "canonicals": made}

