"""Stage 3 — one sprite per skeleton, all the same character."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Any

from ..generation import comfy
from ..generation.comfy import ComfyError
from ..shared import cooling
from ..geometry import props as props_mod
from ..geometry.bodyspace import resolve_view
from ..refs import references as refs_mod
from ..refs.references import Reference, explain, pick
from ..looks import vocabulary
from ..generation.stage import Context, Resource, Stage, opt, register
from .canonical import _anchor_view


def chosen_default(refs) -> float:
    """Identity strength, from the role rather than a magic number."""
    return refs[0].base_weight if refs else 0.85


def _frame_prompt(base: str, entry: dict | None, *,
                  posed: bool) -> tuple[str, str, float]:
    """One frame's prompt. A frame with no skeleton must be told which way to face."""
    yaw = entry["yaw"] if entry is not None else 0.0
    prompt = base if posed else f"{base}, {vocabulary.view_words(yaw)}"
    framing = ((entry.get("crop") or {}) if entry is not None else {}).get("framing")
    if framing:
        prompt = f"{prompt}, {framing}"
    return prompt, vocabulary.facing_negative(yaw), yaw


def _anchor_weight(ip: dict, frame_yaw: float, anchor_yaw: float) -> float:
    """Without this the front canonical outvoted the rear reference on rear frames."""
    weight = float(ip["anchor_weight"])
    falloff = float(ip["anchor_falloff"])
    if falloff <= 0.0:
        return weight
    t = (refs_mod.angular_distance(frame_yaw, anchor_yaw) / 180.0) * falloff
    return weight * (1.0 - t) + float(ip["anchor_far_weight"]) * t


def _frame_inputs(ctx: Context) -> tuple[list, Path, list]:
    """The three artifacts a frame needs, checked to correspond one to one."""
    skeletons: list[Path] = ctx.require("skeletons")
    if not skeletons:
        skeletons = [None] * len(ctx.require("pose_frames"))
    depthmaps: list[Path] = ctx.artifacts.get("depthmaps") or []
    if depthmaps and len(depthmaps) != len(skeletons):
        raise RuntimeError(
            # not-a-message: render_entries() writes both lists one file per
            # pose entry, so a mismatch is a partial write or a version skew,
            # not a config the caller can fix.
            f"{len(depthmaps)} depth maps for {len(skeletons)} skeletons — "
            f"they must correspond one to one.")
    return skeletons, ctx.require("canonical"), depthmaps


def _require_ipadapter(client) -> None:
    missing = [n for n in ("IPAdapterAdvanced", "IPAdapterModelLoader")
               if not client.has_node(n)]
    if missing:
        raise ComfyError(
            f"ComfyUI is missing node(s) {missing}. The IPAdapter_plus custom "
            f"nodes are installed but the server needs a restart to load them.")


def _base_prompt(ctx: Context, cfg: dict) -> tuple[str, str | None]:
    """The prompt every frame starts from, and the backdrop it was built with."""
    held = props_mod.prompt_terms(
        props_mod.load(ctx.config.get("props"), root=ctx.root)
        if props_mod.wanted(ctx.config) else [])
    backdrop = vocabulary.backdrop_colour(ctx.settings("background"))
    prompt = cfg.get("prompt") or vocabulary.prompt_for(
        ctx.config.get("subject") or vocabulary.DEFAULT_SUBJECT,
        ctx.need("rig").prompt_hint,
        ctx.config.get("style") or vocabulary.DEFAULT_STYLE,
        backdrop, held=held)
    return prompt, backdrop


@register
class FramesStage(Stage):
    name = "frames"
    resource = Resource.GPU
    optional = frozenset({"depthmaps", "canonicals"})
    gives = frozenset({"frames"})
    needs = frozenset({"canonical", "pose_frames", "references", "rig", "skeletons"})

    def run(self, ctx: Context, prep: Mapping[str, Any]) -> dict[str, Any]:
        cfg = ctx.settings("frames")
        skeletons, canonical, depthmaps = _frame_inputs(ctx)

        client = comfy.connect(ctx.settings("comfy.host"))
        _require_ipadapter(client)

        match_cfg = ctx.settings("references.match")
        ip = cfg["ip_adapter"]
        lib = ctx.need("references")
        anchor_yaw = resolve_view(_anchor_view(ctx, ctx.settings("canonical")))
        anchor = Reference(path=canonical, yaw=anchor_yaw, label="canonical")

        per_view: dict = ctx.artifacts.get("canonicals") or {}
        if len(per_view) > 1:
            print(f"   {len(per_view)} per-view anchors")

        def anchor_for(yaw: float) -> Reference:
            if not per_view:
                return anchor
            best = min(per_view, key=lambda k: refs_mod.angular_distance(float(k), yaw))
            return Reference(path=per_view[best], yaw=float(best),
                             label=f"canonical@{best}")
        refs = lib.identity or [anchor]
        stack_anchor = bool(lib.identity) and bool(ip["anchor"])

        uploaded = {r.path: client.upload_image(r.path) for r in refs}
        anchor_name = client.upload_image(anchor.path) if stack_anchor else None
        anchor_cache: dict = {}
        style_uploads = [(r, client.upload_image(r.path)) for r in lib.style[:2]]
        if style_uploads:
            print(f"   style from {len(style_uploads)} exemplar(s)")
        tolerance = float(match_cfg["tolerance_degrees"])

        entries: list[dict] = ctx.require("pose_frames")
        base_prompt, backdrop = _base_prompt(ctx, cfg)
        # Measured: at 8 LCM steps the skeleton is only partly obeyed even at end_percent 0.85.
        lcm = bool(cfg["lcm"])
        seed = opt(cfg, "seed", ctx.settings("canonical").get("seed", 1234))
        sampling = comfy.Sampling.from_config(cfg, denoise=cfg["denoise"])

        rig = ctx.need("rig")
        models = ctx.settings("models")
        cn = cfg["controlnet"]
        outdir = ctx.stage_dir("frames")
        written: list[Path] = []

        for i, skeleton in enumerate(skeletons):
            pose_name = client.upload_image(skeleton) if skeleton else None

            entry = entries[i] if i < len(entries) else None
            prompt, facing_neg, frame_yaw = _frame_prompt(
                base_prompt, entry, posed=bool(pose_name))

            negative = vocabulary.negative_for(
                cfg["negative"], backdrop=bool(backdrop),
                pose_control=bool(pose_name), facing=facing_neg,
                guard_skeletons=cfg["guard_against_skeletons"],
                guard_faces=cfg["guard_against_faces"],
            )

            g = comfy.Graph()
            model, pos, neg, vae = comfy.base_graph(
                g,
                prompt=prompt,
                negative=negative,
                lora_strength=cfg["lora_strength"],
                lcm=lcm,
                models=ctx.settings("models"),
            )

            chosen, auto_weight, dist = pick(
                refs, frame_yaw,
                tolerance=tolerance,
                exact_weight=float(opt(match_cfg, "exact_weight",
                                       chosen_default(refs))),
                far_weight=float(match_cfg["far_weight"]),
            )
            auto = bool(match_cfg["auto"])
            weight = (auto_weight if auto
                      else float(ip["weight"]) * chosen.weight_scale)

            if anchor_name:
                _a = anchor_for(frame_yaw)
                if _a.path != anchor.path:
                    if _a.path not in anchor_cache:
                        anchor_cache[_a.path] = client.upload_image(_a.path)
                    anchor_name = anchor_cache[_a.path]
                    anchor_yaw = _a.yaw
                a_weight = _anchor_weight(ip, frame_yaw, anchor_yaw)
                model = comfy.apply_ipadapter(
                    g, model, g.out(g.add("LoadImage", image=anchor_name), 0),
                    weight=a_weight,
                    weight_type=ip["anchor_weight_type"],
                    start_at=0.0, end_at=float(ip["anchor_end_at"]),
                    ipadapter=models.get("ipadapter"),
                )

            ref = g.out(g.add("LoadImage", image=uploaded[chosen.path]), 0)
            model = comfy.apply_ipadapter(
                g, model, ref,
                weight=weight,
                weight_type=ip["weight_type"],
                start_at=ip["start_at"],
                end_at=ip["end_at"],
                ipadapter=models.get("ipadapter"),
            )

            for exemplar, name in style_uploads:
                model = comfy.apply_ipadapter(
                    g, model, g.out(g.add("LoadImage", image=name), 0),
                    weight=refs_mod.style_weight([exemplar], cfg.get("style_weight")),
                    weight_type="style transfer",
                    start_at=0.0, end_at=0.8,
                    ipadapter=models.get("ipadapter"),
                )

            channel = opt(cn, "union_type", None) or rig.skeleton_control
            if not cn["enabled"]:
                channel = None
            if channel and pose_name:
                control = g.out(g.add("LoadImage", image=pose_name), 0)
                pos, neg = comfy.apply_controlnet(
                    g, pos, neg, control, vae,
                    # Measured: 1.0 held to 0.8 makes the model trace the control image and return a stick figure.
                    strength=cn["strength"],
                    start_percent=cn["start_percent"],
                    end_percent=cn["end_percent"],
                    union_type=channel,
                    controlnet=models.get("controlnet"),
                )

            if depthmaps and i < len(depthmaps):
                dcn = cfg["depth_controlnet"]
                depth_name = client.upload_image(depthmaps[i])
                depth_img = g.out(g.add("LoadImage", image=depth_name), 0)
                pos, neg = comfy.apply_controlnet(
                    g, pos, neg, depth_img, vae,
                    strength=dcn["strength"],
                    start_percent=dcn["start_percent"],
                    end_percent=dcn["end_percent"],
                    union_type="depth",
                    controlnet=models.get("controlnet"),
                )

            comfy.sample_and_save(
                g, model, pos, neg, vae,
                sampling=sampling, batch=1, seed=seed,
                prefix=f"{ctx.run_id}_frame{i:03d}",
            )

            images = client.generate(g.build(), timeout=cfg["timeout"])
            dst = outdir / f"frame_{i:03d}.png"
            dst.write_bytes(images[0])
            written.append(dst)
            cooling.rest(ctx.config, after=f"frame {i + 1}",
                         last=i == len(skeletons) - 1)
            print(
                f"   frame {i + 1}/{len(skeletons)} -> {dst.name}  "
                f"[{explain(chosen, weight, dist, tolerance)}"
                f"{' + anchor' if anchor_name else ''}]"
            )

        return {"frames": written}
