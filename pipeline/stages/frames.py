"""Stage 3 — one sprite per skeleton, all the same character.

The consistency recipe is that only ONE input varies across frames:

    same seed          -- same point in latent space
    same prompt        -- same semantic target
    same anchor        -- the canonical sprite, on every frame at equal weight
    DIFFERENT skeleton -- the only thing that changes

Vary anything else and the character drifts. This is why the canonical sprite
has to exist before this stage runs: without a fixed visual reference, each
frame is an independent sample and identity wanders between them.

A supplied identity reference — an illustration of the character — stacks on
top of the anchor rather than replacing it. The two carry different things.
The canonical is already pixel art in the target style and is byte-identical
across frames, so it is what the frames have in common. The illustration holds
a face and a costume at a fidelity the canonical cannot at sprite resolution,
but it is one drawing from one angle, so its weight falls off as the camera
turns away from it. Using only the illustration leaves a rear view steered by
a front drawing at a weak weight and anchored to nothing at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import comfy
from .. import props as props_mod
from .. import rigs as rig_lib
from ..bodyspace import resolve_view
from .. import references as refs_mod
from ..references import Reference, explain, pick
from ..stage import Context, Resource, Stage, opt, register


def chosen_default(refs) -> float:
    """Identity strength, from the role rather than a magic number."""
    return refs[0].base_weight if refs else 0.85


def _view_words(yaw: float) -> str:
    """Plain-language camera direction, for runs with no control image."""
    yaw %= 360
    for limit, words in (
        (22, "front view, facing the viewer"),
        (67, "three-quarter front view"),
        (112, "side view, profile"),
        (157, "three-quarter rear view"),
        (202, "rear view, seen from behind"),
        (247, "three-quarter rear view from the other side"),
        (292, "side view, profile facing the other way"),
        (337, "three-quarter front view from the other side"),
    ):
        if yaw < limit:
            return words
    return "front view, facing the viewer"


@register
class FramesStage(Stage):
    name = "frames"
    resource = Resource.GPU
    requires = frozenset({"skeletons", "canonical", "pose_frames"})
    optional = frozenset({"depthmaps"})
    produces = frozenset({"frames"})

    def run(self, ctx: Context) -> dict[str, Any]:
        cfg = ctx.stage_config("frames")
        skeletons: list[Path] = ctx.require("skeletons")
        entries_for_count: list[dict] = ctx.require("pose_frames")
        # Rig-free runs have no control images, so the loop iterates views.
        if not skeletons:
            skeletons = [None] * len(entries_for_count)
        canonical: Path = ctx.require("canonical")
        depthmaps: list[Path] = ctx.artifacts.get("depthmaps") or []
        if depthmaps and len(depthmaps) != len(skeletons):
            raise RuntimeError(
                f"{len(depthmaps)} depth maps for {len(skeletons)} skeletons — "
                f"they must correspond one to one."
            )

        client = comfy.Client(ctx.config.get("comfy", {}).get("host", "http://127.0.0.1:8188"))
        if not client.alive():
            raise RuntimeError("ComfyUI is not running — start it with ./start.sh")

        missing = [n for n in ("IPAdapterAdvanced", "IPAdapterModelLoader")
                   if not client.has_node(n)]
        if missing:
            raise RuntimeError(
                f"ComfyUI is missing node(s) {missing}. The IPAdapter_plus custom "
                f"nodes are installed but the server needs a restart to load them."
            )

        # Two anchors, not one, and they do different jobs.
        #
        # The canonical is the CONSISTENCY anchor: it is already pixel art, in
        # the target style, and identical for every frame, which is the whole
        # reason this stage requires it. The user's identity references are the
        # DETAIL source: an illustration carries a face, a costume and a colour
        # scheme at a fidelity the canonical cannot hold at sprite resolution.
        #
        # This used to be either/or — supplying any identity reference replaced
        # the canonical entirely — and that quietly undid the stage's own
        # premise. The canonical would be generated from the illustration and
        # then never used, so every frame was steered by a single front-facing
        # illustration and the rear views got it at falloff weight with no
        # pixel-art anchor at all. Frames drifted from each other for exactly
        # the reason the module docstring warns about.
        match_cfg = (ctx.config.get("references") or {}).get("match") or {}
        ip = opt(cfg, "ip_adapter", {})
        lib = ctx.references()
        anchor = Reference(
            path=canonical,
            yaw=resolve_view(
                ctx.stage_config("canonical").get("view")
                or ctx.stage_config("pose").get("view", "side")
            ),
            label="canonical",
        )
        refs = lib.identity or [anchor]
        stack_anchor = bool(lib.identity) and bool(opt(ip, "anchor", True))

        uploaded = {r.path: client.upload_image(r.path) for r in refs}
        anchor_name = client.upload_image(anchor.path) if stack_anchor else None
        style_uploads = [(r, client.upload_image(r.path)) for r in lib.style[:2]]
        if style_uploads:
            print(f"   style from {len(style_uploads)} exemplar(s)")
        tolerance = float(opt(match_cfg, "tolerance_degrees", 40.0))

        entries: list[dict] = ctx.require("pose_frames")
        subject = ctx.config.get("subject", "a knight in armor")
        style = ctx.config.get("style", "pixel art, game sprite, side view, plain flat background")
        hint = ctx.rig().prompt_hint
        # Props are drawn into the depth map AND named here: the geometry says
        # where, the words say what.
        held = props_mod.prompt_terms(props_mod.load(ctx.config.get("props")))
        bg = ctx.config.get("background") or {}
        backdrop = None if opt(bg, "enabled", True) is False else opt(
            bg, "colour", comfy.BACKDROP)
        base_prompt = cfg.get("prompt") or ", ".join(
            p for p in (subject, hint, held, style,
                        comfy.backdrop_prompt(backdrop) if backdrop else "") if p)
        # Pose control needs steps to act in. Measured: at 8 LCM steps the
        # skeleton is only partially obeyed even at end_percent 0.85, because
        # LCM's trajectory settles composition in the first couple of steps and
        # ControlNet cannot redirect it afterwards. At 20 steps the pose lands.
        lcm = bool(opt(cfg, "lcm", False))
        seed = opt(cfg, "seed", ctx.stage_config("canonical").get("seed", 1234))

        rig = ctx.rig()
        # The adapter and ControlNet files are settings for the same reason the
        # checkpoint is: they have to match the checkpoint's family, and a
        # mismatched pair produces plausible nonsense rather than an error.
        models = ctx.config.get("models") or {}
        cn = opt(cfg, "controlnet", {})
        outdir = ctx.stage_dir("frames")
        written: list[Path] = []

        for i, skeleton in enumerate(skeletons):
            pose_name = client.upload_image(skeleton) if skeleton else None

            # Without a skeleton the only thing telling the model which way the
            # subject faces is the words, so the view is spelled out.
            frame_yaw_for_prompt = entries[i]["yaw"] if i < len(entries) else 0.0
            prompt = base_prompt
            if not pose_name:
                prompt = f"{base_prompt}, {_view_words(frame_yaw_for_prompt)}"
            # An annotated reference knows it was cropped; saying so beats
            # letting the model default to a full-body composition.
            framing = ((entries[i].get("crop") or {}) if i < len(entries) else {}).get("framing")
            if framing:
                prompt = f"{prompt}, {framing}"

            # A pose control image invites the model to draw the control image.
            # Naming that failure in the negative is what stops it.
            negative = opt(cfg, "negative", comfy.NEGATIVE)
            if backdrop:
                negative = f"{negative}, {comfy.BACKDROP_NEGATIVE}"
            if pose_name and opt(cfg, "guard_against_skeletons", True):
                negative = f"{negative}, {comfy.POSE_NEGATIVE}"

            g = comfy.Graph()
            model, pos, neg, vae = comfy.base_graph(
                g,
                prompt=prompt,
                negative=negative,
                lora_strength=opt(cfg, "lora_strength", 1.2),
                lcm=lcm,
                models=ctx.config.get("models") or {},
            )

            # Match this frame's viewing angle to the closest labelled
            # reference, and let the weight fall off with angular distance.
            frame_yaw = entries[i]["yaw"] if i < len(entries) else 0.0
            chosen, auto_weight, dist = pick(
                refs, frame_yaw,
                tolerance=tolerance,
                exact_weight=float(opt(match_cfg, "exact_weight",
                                       chosen_default(refs))),
                far_weight=float(opt(match_cfg, "far_weight", 0.45)),
            )
            # Turning off automatic falloff pins every frame to the fixed
            # Identity weight instead of deriving it from angular distance.
            #
            # The per-image scale applies either way. It used to be dropped on
            # the manual path — pick() multiplies it in, and this branch threw
            # that away — so every weight slider in the UI was silently
            # ignored the moment automatic matching was turned off. A control
            # that does nothing is worse than one that is not there.
            auto = bool(opt(match_cfg, "auto", True))
            weight = (auto_weight if auto
                      else float(opt(ip, "weight", 0.85)) * chosen.weight_scale)

            # The anchor goes on first and identically every frame. It is the
            # only input that does not vary, so it is what the frames have in
            # common — which is the definition of staying on-model.
            if anchor_name:
                model = comfy.apply_ipadapter(
                    g, model, g.out(g.add("LoadImage", image=anchor_name), 0),
                    weight=float(opt(ip, "anchor_weight", 0.9)),
                    weight_type="style and composition",
                    start_at=0.0, end_at=1.0,
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

            # Only the humanoid rig has a matching OpenPose model. Every other
            # topology goes out as a scribble, and a rig with no skeleton
            # channel at all (a blob) skips this pass entirely and relies on
            # depth, which we compute ourselves and which has no such limit.
            # Measured on a standing character sheet: with the skeleton
            # channel on, legs came back as white shafts with ball joints —
            # the guide drawn as bones. With depth alone they came back as
            # armoured legs with boots. A standing pose needs no skeleton;
            # an attack does.
            # Style exemplars sit on top of identity at a much lower weight:
            # they say how the art should look, not who the character is.
            for exemplar, name in style_uploads:
                model = comfy.apply_ipadapter(
                    g, model, g.out(g.add("LoadImage", image=name), 0),
                    weight=refs_mod.style_weight([exemplar], opt(cfg, "style_weight", None)),
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
                    # Measured: 1.0 held to 0.8 makes the model trace the
                    # control image and return a stick figure. 0.75 to 0.55
                    # still lands the pose while leaving the LoRA room to
                    # render an actual character over it.
                    strength=opt(cn, "strength", 0.75),
                    start_percent=opt(cn, "start_percent", 0.0),
                    end_percent=opt(cn, "end_percent", 0.55),
                    union_type=channel,
                    controlnet=models.get("controlnet"),
                )

            # Depth stacks on top of pose when the depth stage ran. Both use
            # the same Union model, so this is a second conditioning pass
            # rather than a second set of weights. Depth carries the viewing
            # angle, which a skeleton alone cannot express.
            if depthmaps and i < len(depthmaps):
                dcn = opt(cfg, "depth_controlnet", {})
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
                width=opt(cfg, "width", 1024), height=opt(cfg, "height", 1024),
                batch=1,
                seed=seed,                      # identical for every frame
                steps=opt(cfg, "steps", 8 if lcm else 25),
                cfg=opt(cfg, "cfg", 1.5 if lcm else 7.0),
                lcm=lcm,
                denoise=opt(cfg, "denoise", 1.0),
                sampler=opt(cfg, "sampler", None),
                scheduler=opt(cfg, "scheduler", None),
                prefix=f"{ctx.run_id}_frame{i:03d}",
            )

            images = client.generate(g.build(), timeout=opt(cfg, "timeout", 1800))
            dst = outdir / f"frame_{i:03d}.png"
            dst.write_bytes(images[0])
            written.append(dst)
            print(
                f"   frame {i + 1}/{len(skeletons)} -> {dst.name}  "
                f"[{explain(chosen, weight, dist, tolerance)}"
                f"{' + anchor' if anchor_name else ''}]"
            )

        return {"frames": written}
