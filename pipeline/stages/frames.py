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
from .. import cooling
from .. import props as props_mod
from .. import rigs as rig_lib
from ..bodyspace import resolve_view
from .. import references as refs_mod
from ..references import Reference, explain, pick
from ..stage import Context, Resource, Stage, opt, register
# One definition of which way the anchor faces, shared with the stage that
# rendered it. Two copies drifted once already.
from .canonical import _anchor_view


def chosen_default(refs) -> float:
    """Identity strength, from the role rather than a magic number."""
    return refs[0].base_weight if refs else 0.85


def _facing_negative(yaw: float) -> str:
    """Words that stop a face being drawn on a view that has none.

    Depth cannot say which way a head points - it renders the head as a
    capsule, and measured on this rig a front and a rear depth map differ by
    about 6% across the head band while the two SIDE maps are bit-identical
    under mirroring. The channel that CAN say it is the OpenPose skeleton,
    whose face keypoints drop past ~100 degrees, and a standing character sheet
    turns that channel off because it draws the guide as bones.

    So on a rear frame nothing geometric says "no face" and the model draws one
    anyway. Naming the failure in the negative is this project's existing
    answer to exactly that shape of problem - it is how the stick-figure
    tracing was stopped - and it costs nothing.
    """
    yaw %= 360
    if 135 <= yaw <= 225:
        return "face, eyes, nose, mouth, facial features, front view"
    if 100 < yaw < 135 or 225 < yaw < 260:
        return "both eyes visible, front-facing face"
    return ""


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
    optional = frozenset({"depthmaps", "canonicals"})
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
        # Ask canonical which way it actually rendered the anchor. This used to
        # repeat the old fallthrough - canonical.view, else pose.view, else the
        # literal "side" - which DECISIONS.md records as a real failure and
        # canonical.py already fixed by falling through to pose.set[0].view.
        # Only one copy was fixed, so a sheet whose first view is front had its
        # anchor recorded here as facing 90 degrees, and every falloff measured
        # from that wrong origin: the front reference read as 90 away on the
        # front frame, and the rear reference as 90 away on the rear frame.
        anchor_yaw = resolve_view(_anchor_view(ctx, ctx.stage_config("canonical")))
        anchor = Reference(path=canonical, yaw=anchor_yaw, label="canonical")

        # Per-view anchors, when the canonical stage made them. Each frame gets
        # the anchor at ITS angle instead of every frame inheriting the front.
        per_view: dict = ctx.artifacts.get("canonicals") or {}
        if len(per_view) > 1:
            print(f"   {len(per_view)} per-view anchors")

        def anchor_for(yaw: float) -> Reference:
            """The anchor nearest this frame's angle.

            Deliberately NOT falling back to the front canonical on a miss: a
            silent fallback is exactly how every view came back front-facing in
            the first place, and it hides in the output rather than the log.
            With one anchor this returns it and the falloff handles the rest.
            """
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
        style = ctx.config.get("style", "pixel art, game sprite, side view, plain flat background")
        hint = ctx.rig().prompt_hint
        # Props are drawn into the depth map AND named here: the geometry says
        # where, the words say what.
        held = props_mod.prompt_terms(
            props_mod.load(ctx.config.get("props"), root=ctx.root)
            if props_mod.wanted(ctx) else [])
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
            facing_neg = _facing_negative(frame_yaw_for_prompt)
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
            # Nothing geometric says "this view has no face" once the
            # skeleton channel is off, so the words have to.
            if facing_neg and opt(cfg, "guard_against_faces", True):
                negative = f"{negative}, {facing_neg}"

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
            #
            # weight_type is `linear`, NOT `style and composition`. The anchor
            # is a front-facing canonical, and `style and composition` carries
            # composition: at 0.9 over the whole sample it outweighed the depth
            # map that carries yaw (0.45, and only to 60%), so a side frame came
            # back front-facing and the canonical's grey backdrop bled over the
            # magenta the prompt asked for. base_pixel.yaml already rejected
            # that weight_type for the identity reference and for the same
            # reason; the anchor had it hardcoded and ignored the config.
            #
            # Keeping the weight high is deliberate - measured, an anchor at
            # 0.55 under an 0.85 identity reference lost on rendering and the
            # frames drifted. The fix is to drop composition, not strength.
            if anchor_name:
                _a = anchor_for(frame_yaw)
                if _a.path != anchor.path:
                    if _a.path not in anchor_cache:
                        anchor_cache[_a.path] = client.upload_image(_a.path)
                    anchor_name = anchor_cache[_a.path]
                    anchor_yaw = _a.yaw
                # Reference weight falls off with angular distance; the anchor's
                # did not. On a rear frame the rear reference was down-weighted
                # for being far from that view while the FRONT canonical stayed
                # at 0.9, so the anchor outvoted the one image that actually
                # shows the back of the costume. anchor_falloff scales the
                # anchor by the same logic: 0.0 keeps the old fixed behaviour,
                # 1.0 drops it to anchor_far_weight at 180 degrees away.
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
            cooling.rest(ctx.config, after=f"frame {i + 1}",
                         last=i == len(skeletons) - 1)
            print(
                f"   frame {i + 1}/{len(skeletons)} -> {dst.name}  "
                f"[{explain(chosen, weight, dist, tolerance)}"
                f"{' + anchor' if anchor_name else ''}]"
            )

        return {"frames": written}
