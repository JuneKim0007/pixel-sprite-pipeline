"""Stage 2 — the canonical sprite: one image that defines who the character is."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Any

from ..generation import comfy
from ..shared import cooling
from ..geometry.bodyspace import resolve_view
from ..refs import references as refs_mod
from ..looks import vocabulary
from ..generation.stage import Context, Resource, Stage, opt, register


@dataclass
class _Conditioning:
    """What one anchor view is conditioned on: an identity image, and control channels."""

    chosen: Any
    control_names: dict


def _label_for(yaw: float) -> str:
    """Named views only span 0-180, so the character's right side has no name and becomes its angle."""
    from ..geometry.bodyspace import VIEWS
    for name, deg in VIEWS.items():
        if abs(deg - refs_mod.bearing(yaw)) < 0.5:
            return name
    return f"{refs_mod.bearing(yaw):03d}"


def _anchor_view(ctx, cfg) -> str | float:
    """Which way the anchor faces. Three sources, most specific first."""
    explicit = cfg.get("view")
    if explicit is not None:
        return explicit

    pose_cfg = ctx.settings("pose")
    named = opt(pose_cfg, "view", None)
    if named is not None:
        return named

    first = (opt(pose_cfg, "set", []) or [{}])[0]
    return first.get("view", "side") if isinstance(first, dict) else "side"


@register
class CanonicalStage(Stage):
    name = "canonical"
    resource = Resource.GPU
    DEFAULTS = {"from_reference": {}}
    optional = frozenset({"skeletons", "depthmaps", "pose_frames"})
    gives = frozenset({"canonical", "canonicals"})
    needs = frozenset({"references", "rig"})

    def run(self, ctx: Context, prep: Mapping[str, Any]) -> dict[str, Any]:
        cfg = ctx.settings("canonical")
        subject = ctx.config.get("subject") or vocabulary.DEFAULT_SUBJECT
        client = comfy.connect(ctx.settings("comfy.host"))

        style = ctx.config.get("style") or vocabulary.DEFAULT_STYLE
        backdrop = vocabulary.backdrop_colour(ctx.settings("background"))
        prompt = cfg.get("prompt") or vocabulary.prompt_for(
            subject, ctx.need("rig").prompt_hint, style, backdrop)
        lcm = bool(cfg["lcm"])

        lib = ctx.need("references")
        from_ref = cfg["from_reference"] or {}
        want_view = resolve_view(_anchor_view(ctx, cfg))

        skeletons = ctx.artifacts.get("skeletons") or []
        depthmaps = ctx.artifacts.get("depthmaps") or []
        cn = cfg["controlnet"]

        def _conditioning(view: float) -> _Conditioning:
            chosen = None
            if lib.identity and opt(from_ref, "enabled", True):
                chosen, _, away = refs_mod.pick(lib.identity, view, tolerance=180.0)
                print(f"   identity from {chosen.label} ({away:.0f}deg away)")

            entries = ctx.artifacts.get("pose_frames") or []
            i = min(range(len(entries)),
                    key=lambda k: refs_mod.angular_distance(
                        float((entries[k] or {}).get("yaw", 0.0)), view)) if entries else 0
            guide = skeletons[i] if i < len(skeletons) else (skeletons[0] if skeletons else None)
            depth = depthmaps[i] if i < len(depthmaps) else (depthmaps[0] if depthmaps else None)

            names: dict = {}
            if bool(cn["enabled"]) and (guide or depth):
                channel = opt(cn, "union_type", None) or ctx.need("rig").skeleton_control
                if guide and channel:
                    names["pose"] = (client.upload_image(guide), channel)
                if depth:
                    names["depth"] = (client.upload_image(depth), "depth")
                origin = (entries[0] or {}).get("from_annotation") if entries else None
                where = (f"annotation of {Path(origin).name}" if origin
                         else f"{ctx.settings('pose').get('source', 'library')} pose, "
                              f"{ctx.need('rig').label}")
                print(f"   conditioned by {', '.join(names) or 'nothing'}  <- {where}")
            return _Conditioning(chosen, names)

        # Measured: batch 1806 s / 1.28M swap-ins against sequential 2096 s / 2.8M.
        uploads: dict = {}

        def build(cond: _Conditioning):
            g = comfy.Graph()
            model, pos, neg, vae = comfy.base_graph(
                g,
                prompt=prompt,
                negative=vocabulary.negative_for(
                    cfg["negative"], backdrop=bool(backdrop),
                    pose_control=bool(cond.control_names.get("pose"))),
                lora_strength=cfg["lora_strength"],
                lcm=lcm,
                models=ctx.settings("models"),
            )

            if cond.chosen is not None:
                chosen = cond.chosen
                if chosen.path not in uploads:
                    uploads[chosen.path] = client.upload_image(chosen.path)
                ref_img = g.out(g.add("LoadImage", image=uploads[chosen.path]), 0)
                model = comfy.apply_ipadapter(
                    g, model, ref_img,
                    weight=float(opt(from_ref, "weight", chosen.base_weight)),
                    weight_type=opt(from_ref, "weight_type", "linear"),
                    start_at=0.0, end_at=1.0,
                    ipadapter=ctx.settings("models.ipadapter"),
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
                    ipadapter=ctx.settings("models.ipadapter"),
                )
            for kind, (name, channel) in cond.control_names.items():
                control = g.out(g.add("LoadImage", image=name), 0)
                strong = kind == "pose"
                pos, neg = comfy.apply_controlnet(
                    g, pos, neg, control, vae,
                    strength=opt(cn, "strength", 0.55 if strong else 0.30),
                    start_percent=cn["start_percent"],
                    end_percent=opt(cn, "end_percent", 0.40 if strong else 0.35),
                    union_type=channel,
                    controlnet=ctx.settings("models.controlnet"),
                )
            return g, model, pos, neg, vae

        if lib.style:
            print(f"   style from {len(lib.style[:2])} exemplar(s)")

        base_seed = cfg["seed"]
        timeout = cfg["timeout"]
        sampling = comfy.Sampling.from_config(cfg)

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
            cond = _conditioning(view)
            wanted = max(1, int(cfg["candidates"]))
            images: list[bytes] = []

            if bool(cfg["batch_candidates"]) and wanted > 1:
                g, model, pos, neg, vae = build(cond)
                comfy.sample_and_save(
                    g, model, pos, neg, vae,
                    sampling=sampling, batch=wanted, seed=base_seed,
                    prefix=f"{ctx.run_id}_canonical",
                )
                print(f"   {wanted} candidates as one batch")
                images = client.generate(g.build(), timeout=timeout)
                wanted = 0

            for n in range(wanted):
                g, model, pos, neg, vae = build(cond)
                comfy.sample_and_save(
                    g, model, pos, neg, vae,
                    sampling=sampling, batch=1, seed=base_seed + n,
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
            made[refs_mod.bearing(view)] = dst
            if primary is None:
                primary = dst
            print(f"   canonical -> {dst.relative_to(ctx.root)}  (seed {base_seed})")
            if len(views) > 1:
                cooling.rest(ctx.config, after=f"anchor {vi + 1}",
                             last=vi == len(views) - 1)

        return {"canonical": primary, "canonicals": made}

