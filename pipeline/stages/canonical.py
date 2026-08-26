"""Stage 2 — the canonical sprite: one image that defines who the character is."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Any

from ..generation import comfy
from ..generation.comfy import ComfyError
from ..shared import cooling
from ..geometry import rigs as rig_lib
from ..geometry.bodyspace import resolve_view
from ..refs import references as refs_mod
from ..looks import vocabulary
from ..generation.stage import Context, Resource, Stage, opt, register


def _label_for(yaw: float) -> str:
    """Named views only span 0-180, so the character's right side has no name and becomes its angle."""
    from ..geometry.bodyspace import VIEWS
    for name, deg in VIEWS.items():
        if abs(deg - (round(yaw) % 360)) < 0.5:
            return name
    return f"{round(yaw) % 360:03d}"


def _anchor_view(ctx, cfg) -> str | float:
    """[...] for a sheet whose first view is front, and a reference labelled `front` was then measured as 90 degrees away and down-weighted by the falloff for being a poor match"""
    explicit = cfg.get("view")
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
    DEFAULTS = {"from_reference": {}, "controlnet": {}}
    optional = frozenset({"skeletons", "depthmaps", "pose_frames"})
    gives = frozenset({"canonical", "canonicals"})
    needs = frozenset({"references", "rig"})

    def run(self, ctx: Context, prep: Mapping[str, Any]) -> dict[str, Any]:
        cfg = ctx.stage_config("canonical")
        subject = ctx.config.get("subject", "a knight in armor")
        client = comfy.Client(ctx.config.get("comfy", {}).get("host", "http://127.0.0.1:8188"))
        if not client.alive():
            raise ComfyError("ComfyUI is not running — start it with ./start.sh")

        default_style = vocabulary.DEFAULT_STYLE
        style = ctx.config.get("style", default_style)
        hint = ctx.need("rig").prompt_hint
        bg = ctx.config.get("background") or {}
        backdrop = None if opt(bg, "enabled", True) is False else opt(
            bg, "colour", vocabulary.BACKDROP)
        prompt = cfg.get("prompt") or ", ".join(
            p for p in (subject, hint, style,
                        vocabulary.backdrop_prompt(backdrop) if backdrop else "") if p)
        lcm = bool(cfg["lcm"])

        lib = ctx.need("references")
        from_ref = cfg["from_reference"] or {}
        want_view = resolve_view(_anchor_view(ctx, cfg))

        def _prepare(view: float) -> None:
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
                ch = opt(cn, "union_type", None) or ctx.need("rig").skeleton_control
                if guide and ch:
                    control_names["pose"] = (client.upload_image(guide), ch)
                if depth:
                    control_names["depth"] = (client.upload_image(depth), "depth")

        chosen = None
        if lib.identity and opt(from_ref, "enabled", True):
            chosen, _, dist = refs_mod.pick(lib.identity, want_view, tolerance=180.0)
            print(f"   identity from {chosen.label} ({dist:.0f}deg away)")

        # Three symptoms came from that one gap: duplicate props (told only "holding a bow", the model decides where it goes and sometimes decides twice), broken anatomy, and degradation [...]
        skeletons = ctx.artifacts.get("skeletons") or []
        depthmaps = ctx.artifacts.get("depthmaps") or []

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
        cn = cfg["controlnet"]
        use_control = bool(opt(cn, "enabled", True)) and (guide or depth)

        control_names: dict = {}
        if use_control:
            channel = opt(cn, "union_type", None) or ctx.need("rig").skeleton_control
            if guide and channel:
                control_names["pose"] = (client.upload_image(guide), channel)
            if depth:
                control_names["depth"] = (client.upload_image(depth), "depth")
            entries = ctx.artifacts.get("pose_frames") or []
            origin = (entries[0] or {}).get("from_annotation") if entries else None
            source = ctx.stage_config("pose").get("source", "library")
            where = (f"annotation of {Path(origin).name}" if origin
                     else f"{source} pose, {ctx.need("rig").label}")
            print(f"   conditioned by {', '.join(control_names) or 'nothing'}"
                  f"  <- {where}")

        # Measured: batch 1806 s and 1.28M swap-ins against sequential 2096 s and 2.8M, and candidate 0 is byte-identical [...]
        uploads: dict = {}

        def build():
            g = comfy.Graph()
            model, pos, neg, vae = comfy.base_graph(
                g,
                prompt=prompt,
                negative=vocabulary.negative_for(
                    cfg["negative"], backdrop=bool(backdrop),
                    pose_control=bool(control_names.get("pose"))),
                lora_strength=cfg["lora_strength"],
                lcm=lcm,
                models=ctx.config.get("models") or {},
            )

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

            for exemplar in lib.style[:2]:
                if exemplar.path not in uploads:
                    uploads[exemplar.path] = client.upload_image(exemplar.path)
                model = comfy.apply_ipadapter(
                    g, model, g.out(g.add("LoadImage", image=uploads[exemplar.path]), 0),
                    weight=refs_mod.style_weight(
                        [exemplar], cfg.get("style_weight")),
                    weight_type="style transfer",
                    start_at=0.0, end_at=0.8,
                    ipadapter=(ctx.config.get("models") or {}).get("ipadapter"),
                )
            for kind, (name, channel) in control_names.items():
                control = g.out(g.add("LoadImage", image=name), 0)
                strong = kind == "pose"
                pos, neg = comfy.apply_controlnet(
                    g, pos, neg, control, vae,
                    strength=opt(cn, "strength", 0.55 if strong else 0.30),
                    start_percent=opt(cn, "start_percent", 0.0),
                    end_percent=opt(cn, "end_percent", 0.40 if strong else 0.35),
                    union_type=channel,
                    controlnet=(ctx.config.get("models") or {}).get("controlnet"),
                )
            return g, model, pos, neg, vae

        if lib.style:
            print(f"   style from {len(lib.style[:2])} exemplar(s)")

        base_seed = cfg["seed"]
        timeout = cfg["timeout"]

        _entries_all = ctx.artifacts.get("pose_frames") or []
        if bool(cfg["per_view"]) and _entries_all:
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
            wanted = max(1, int(cfg["candidates"]))
            images: list[bytes] = []

            if bool(cfg["batch_candidates"]) and wanted > 1:
                g, model, pos, neg, vae = build()
                comfy.sample_and_save(
                    g, model, pos, neg, vae,
                    width=cfg["width"], height=cfg["height"],
                    batch=wanted,
                    seed=base_seed,
                    steps=opt(cfg, "steps", 8 if lcm else 25),
                    cfg=opt(cfg, "cfg", 1.5 if lcm else 7.0),
                    lcm=lcm,
                    denoise=1.0,
                    sampler=cfg.get("sampler"),
                    scheduler=cfg.get("scheduler"),
                    prefix=f"{ctx.run_id}_canonical",
                )
                print(f"   {wanted} candidates as one batch")
                images = client.generate(g.build(), timeout=timeout)
                wanted = 0

            for n in range(wanted):
                g, model, pos, neg, vae = build()
                comfy.sample_and_save(
                    g, model, pos, neg, vae,
                    width=cfg["width"], height=cfg["height"],
                    batch=1,
                    seed=base_seed + n,
                    steps=opt(cfg, "steps", 8 if lcm else 25),
                    cfg=opt(cfg, "cfg", 1.5 if lcm else 7.0),
                    lcm=lcm,
                    denoise=1.0,
                    sampler=cfg.get("sampler"),
                    scheduler=cfg.get("scheduler"),
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

