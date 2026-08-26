"""Stage 3 — one sprite per skeleton, all the same character."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Any

from ..generation import comfy
from ..generation.comfy import ComfyError
from ..shared import cooling
from ..geometry import props as props_mod
from ..geometry import rigs as rig_lib
from ..geometry.bodyspace import resolve_view
from ..refs import references as refs_mod
from ..refs.references import Reference, explain, pick
from ..looks import vocabulary
from ..generation.stage import Context, Resource, Stage, opt, register
from .canonical import _anchor_view


def chosen_default(refs) -> float:
    """Identity strength, from the role rather than a magic number."""
    return refs[0].base_weight if refs else 0.85


@register
class FramesStage(Stage):
    name = "frames"
    resource = Resource.GPU
    DEFAULTS = {
        "ip_adapter": {}, "lcm": False, "controlnet": {},
        "negative": vocabulary.NEGATIVE, "guard_against_skeletons": True,
        "guard_against_faces": True, "lora_strength": 1.2,
        "depth_controlnet": {}, "width": 1024, "height": 1024,
        "denoise": 1.0, "timeout": 1800,
    }
    requires = frozenset({"skeletons", "canonical", "pose_frames"})
    optional = frozenset({"depthmaps", "canonicals"})
    produces = frozenset({"frames"})

    def run(self, ctx: Context, prep: Mapping[str, Any]) -> dict[str, Any]:
        cfg = ctx.stage_config("frames")
        skeletons: list[Path] = ctx.require("skeletons")
        entries_for_count: list[dict] = ctx.require("pose_frames")
        if not skeletons:
            skeletons = [None] * len(entries_for_count)
        canonical: Path = ctx.require("canonical")
        depthmaps: list[Path] = ctx.artifacts.get("depthmaps") or []
        if depthmaps and len(depthmaps) != len(skeletons):
            raise RuntimeError(
                # not-a-message: both lists are written one file per pose entry
                # by render_entries(), from the same pose_frames — a length
                # mismatch means a partial write or a stage version skew, not
                # a config the caller can fix.
                f"{len(depthmaps)} depth maps for {len(skeletons)} skeletons — "
                f"they must correspond one to one."
            )

        client = comfy.Client(ctx.config.get("comfy", {}).get("host", "http://127.0.0.1:8188"))
        if not client.alive():
            raise ComfyError("ComfyUI is not running — start it with ./start.sh")

        missing = [n for n in ("IPAdapterAdvanced", "IPAdapterModelLoader")
                   if not client.has_node(n)]
        if missing:
            raise ComfyError(
                f"ComfyUI is missing node(s) {missing}. The IPAdapter_plus custom "
                f"nodes are installed but the server needs a restart to load them."
            )

        match_cfg = (ctx.config.get("references") or {}).get("match") or {}
        ip = cfg["ip_adapter"]
        lib = ctx.references()
        anchor_yaw = resolve_view(_anchor_view(ctx, ctx.stage_config("canonical")))
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
        stack_anchor = bool(lib.identity) and bool(opt(ip, "anchor", True))

        uploaded = {r.path: client.upload_image(r.path) for r in refs}
        anchor_name = client.upload_image(anchor.path) if stack_anchor else None
        anchor_cache: dict = {}
        style_uploads = [(r, client.upload_image(r.path)) for r in lib.style[:2]]
        if style_uploads:
            print(f"   style from {len(style_uploads)} exemplar(s)")
        tolerance = float(opt(match_cfg, "tolerance_degrees", 40.0))

        entries: list[dict] = ctx.require("pose_frames")
        subject = ctx.config.get("subject", "a knight in armor")
        style = opt(ctx.config, "style", vocabulary.DEFAULT_STYLE)
        hint = ctx.rig().prompt_hint
        held = props_mod.prompt_terms(
            props_mod.load(ctx.config.get("props"), root=ctx.root)
            if props_mod.wanted(ctx) else [])
        bg = ctx.config.get("background") or {}
        backdrop = None if opt(bg, "enabled", True) is False else opt(
            bg, "colour", vocabulary.BACKDROP)
        base_prompt = cfg.get("prompt") or ", ".join(
            p for p in (subject, hint, held, style,
                        vocabulary.backdrop_prompt(backdrop) if backdrop else "") if p)
        # Measured: at 8 LCM steps the skeleton is only partially obeyed even at end_percent 0.85, because LCM's trajectory settles composition in the first couple of steps and [...]
        lcm = bool(cfg["lcm"])
        seed = opt(cfg, "seed", ctx.stage_config("canonical").get("seed", 1234))

        rig = ctx.rig()
        models = ctx.config.get("models") or {}
        cn = cfg["controlnet"]
        outdir = ctx.stage_dir("frames")
        written: list[Path] = []

        for i, skeleton in enumerate(skeletons):
            pose_name = client.upload_image(skeleton) if skeleton else None

            frame_yaw_for_prompt = entries[i]["yaw"] if i < len(entries) else 0.0
            prompt = base_prompt
            if not pose_name:
                prompt = f"{base_prompt}, {vocabulary.view_words(frame_yaw_for_prompt)}"
            facing_neg = vocabulary.facing_negative(frame_yaw_for_prompt)
            framing = ((entries[i].get("crop") or {}) if i < len(entries) else {}).get("framing")
            if framing:
                prompt = f"{prompt}, {framing}"

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
                models=ctx.config.get("models") or {},
            )

            frame_yaw = entries[i]["yaw"] if i < len(entries) else 0.0
            chosen, auto_weight, dist = pick(
                refs, frame_yaw,
                tolerance=tolerance,
                exact_weight=float(opt(match_cfg, "exact_weight",
                                       chosen_default(refs))),
                far_weight=float(opt(match_cfg, "far_weight", 0.45)),
            )
            auto = bool(opt(match_cfg, "auto", True))
            weight = (auto_weight if auto
                      else float(opt(ip, "weight", 0.85)) * chosen.weight_scale)

            if anchor_name:
                _a = anchor_for(frame_yaw)
                if _a.path != anchor.path:
                    if _a.path not in anchor_cache:
                        anchor_cache[_a.path] = client.upload_image(_a.path)
                    anchor_name = anchor_cache[_a.path]
                    anchor_yaw = _a.yaw
                # [...] was down-weighted for being far from that view while the FRONT canonical stayed at 0.9, so the anchor outvoted the one image that actually shows the back of the costume
                a_weight = float(opt(ip, "anchor_weight", 0.9))
                falloff = float(opt(ip, "anchor_falloff", 0.0))
                if falloff > 0.0:
                    away = abs((frame_yaw - anchor_yaw + 180.0) % 360.0 - 180.0)
                    far = float(opt(ip, "anchor_far_weight", 0.5))
                    t = (away / 180.0) * falloff
                    a_weight = a_weight * (1.0 - t) + far * t
                model = comfy.apply_ipadapter(
                    g, model, g.out(g.add("LoadImage", image=anchor_name), 0),
                    weight=a_weight,
                    weight_type=opt(ip, "anchor_weight_type", "linear"),
                    start_at=0.0, end_at=float(opt(ip, "anchor_end_at", 1.0)),
                    ipadapter=models.get("ipadapter"),
                )

            ref = g.out(g.add("LoadImage", image=uploaded[chosen.path]), 0)
            model = comfy.apply_ipadapter(
                g, model, ref,
                weight=weight,
                weight_type=opt(ip, "weight_type", "linear"),
                start_at=opt(ip, "start_at", 0.0),
                end_at=opt(ip, "end_at", 1.0),
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
            if not opt(cn, "enabled", True):
                channel = None
            if channel and pose_name:
                control = g.out(g.add("LoadImage", image=pose_name), 0)
                pos, neg = comfy.apply_controlnet(
                    g, pos, neg, control, vae,
                    # Measured: 1.0 held to 0.8 makes the model trace the control image and return a stick figure.
                    strength=opt(cn, "strength", 0.75),
                    start_percent=opt(cn, "start_percent", 0.0),
                    end_percent=opt(cn, "end_percent", 0.55),
                    union_type=channel,
                    controlnet=models.get("controlnet"),
                )

            if depthmaps and i < len(depthmaps):
                dcn = cfg["depth_controlnet"]
                depth_name = client.upload_image(depthmaps[i])
                depth_img = g.out(g.add("LoadImage", image=depth_name), 0)
                pos, neg = comfy.apply_controlnet(
                    g, pos, neg, depth_img, vae,
                    strength=opt(dcn, "strength", 0.45),
                    start_percent=opt(dcn, "start_percent", 0.0),
                    end_percent=opt(dcn, "end_percent", 0.6),
                    union_type="depth",
                    controlnet=models.get("controlnet"),
                )

            comfy.sample_and_save(
                g, model, pos, neg, vae,
                width=cfg["width"], height=cfg["height"],
                batch=1,
                seed=seed,
                steps=opt(cfg, "steps", 8 if lcm else 25),
                cfg=opt(cfg, "cfg", 1.5 if lcm else 7.0),
                lcm=lcm,
                denoise=cfg["denoise"],
                sampler=cfg.get("sampler"),
                scheduler=cfg.get("scheduler"),
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
