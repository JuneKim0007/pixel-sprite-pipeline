"""Stage 1 — produce skeleton control images.

Skeletons are authored or generated in body space, then projected to the
requested viewing angle. Nothing here is estimated from an image: pose
estimators are trained on photographs and fail on sprites, which is why the
skeleton is an input to generation rather than something recovered from it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..geometry import props as props_mod
from ..geometry import rigs as rig_lib
from ..geometry.bodyspace import frame_scale, project, resolve_view, validate_pose
from ..geometry.openpose import render
from ..generation.stage import Context, Resource, Stage, opt, register


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48] or "pose"


@register
class PoseStage(Stage):
    name = "pose"
    resource = Resource.CPU
    # `pose_frames` is the body-space data behind the images, published so the
    # optional depth stage can render a second view of the same pose without
    # re-deriving it from pixels.
    produces = frozenset({"skeletons", "pose_frames"})

    def run(self, ctx: Context) -> dict[str, Any]:
        cfg = ctx.stage_config("pose")
        size = opt(cfg, "size", 1024)
        outdir = ctx.stage_dir("pose")
        # The rig decides joint layout, bone hierarchy and which ControlNet
        # channel the skeleton can legitimately be sent to.
        rig = ctx.rig()

        # Two ways to produce skeletons, one code path.
        #
        #   animation  one spec, N temporally related frames of a single action
        #   pose set   N independent specs, one frame each, optionally each at
        #              its own viewing angle (a turnaround is the same pose
        #              rendered at several yaws)
        #
        # Nothing downstream distinguishes them: `frames` generates one sprite
        # per skeleton against a shared reference either way. Which is why this
        # is a different input rather than a different mode — a mode toggle
        # would duplicate the pipeline to no purpose.
        specs = cfg.get("set")
        # Views can follow the references instead of a fixed list. With art
        # shot from arbitrary angles, generating the four textbook views means
        # three of them have no evidence behind them.
        if specs == "from_references" or opt(cfg, "views", "") == "from_references":
            specs = self._views_from_references(ctx)
        entries: list[dict[str, Any]] = []

        if specs:
            for i, spec in enumerate(specs):
                if not isinstance(spec, dict):
                    raise ValueError(f"pose.set[{i}] must be a mapping")
                merged = {**cfg, **spec}
                merged.pop("set", None)
                pick = spec.get("frame")
                # When picking frame N, load the whole sequence first —
                # truncating to one frame before indexing would make any
                # index above 0 unreachable.
                got = self._resolve(
                    ctx, merged, wanted=None if pick is not None else spec.get("frames", 1)
                )
                if pick is not None:
                    if not 0 <= pick < len(got):
                        raise IndexError(
                            f"pose.set[{i}].frame={pick} but that pose has "
                            f"{len(got)} frame(s)"
                        )
                    got = [got[pick]]
                yaw = resolve_view(merged.get("view", "side"))
                entries += [{"pose": p, "yaw": yaw, "spec": i} for p in got]
        else:
            got = self._resolve(ctx, cfg, wanted=cfg.get("frames"))
            if got and isinstance(got[0], dict) and "annotation" in got[0]:
                entries = [{**g, "spec": 0} for g in got]
            else:
                yaw = resolve_view(opt(cfg, "view", "side"))
                entries = [{"pose": p, "yaw": yaw, "spec": 0} for p in got]

        written: list[Path] = []
        if not rig.joints:
            # Rig-free: nothing to draw. The views still exist as angles, and
            # the frames stage turns them into prompt terms instead of control
            # images.
            (outdir / "pose.json").write_text(
                json.dumps({"source": "none", "rig": rig.name, "mode": "rig_free",
                            "entries": entries}, indent=1))
            print(f"   rig-free: {len(entries)} view(s), steered by prompt")
            return {"skeletons": [], "pose_frames": entries}

        for i, entry in enumerate(entries):
            annotation = entry.get("annotation")
            if annotation is not None:
                # Already in screen space — projecting would be meaningless.
                from ..geometry import annotate as ann

                dst = outdir / f"skeleton_{i:03d}.png"
                ann.render(annotation, size, size).save(dst)
                written.append(dst)
                entry["pose"] = {}
                entry["from_annotation"] = str(annotation.image)
                entry["crop"] = ann.infer_crop(annotation)
                continue

            props = props_mod.load(ctx.config.get("props"), root=ctx.root)
            if props and not props_mod.wanted(ctx):
                props = []
            if props:
                entry["pose"] = props_mod.pull_second_hand(props, entry["pose"], rig)
            fill = float(opt(cfg, "fill", 0.0) or 0.0)
            keypoints = project(
                entry["pose"], entry["yaw"],
                depth_scale=opt(cfg, "depth_scale", 1.0),
                lateral_scale=opt(cfg, "lateral_scale", 1.0),
                fill=fill,
                rig=rig,
            )
            dst = outdir / f"skeleton_{i:03d}.png"
            # Sticks thicken with the figure, or a larger skeleton is drawn out
            # of the same thin lines and reads as a wiry character.
            grow = frame_scale(entry["pose"], fill)
            thickness = cfg.get("thickness")
            render(keypoints, size, size, rig=rig,
                   thickness=(thickness * grow) if thickness else None).save(dst)
            written.append(dst)

        serialisable = [
            {k: v for k, v in e.items() if k != "annotation"} for e in entries
        ]
        (outdir / "pose.json").write_text(
            json.dumps(
                {"source": opt(cfg, "source", "library"),
                 "rig": rig.name,
                 "rig_choice": ctx.artifacts.get("_rig_record", {}),
                 "skeleton_control": rig.skeleton_control,
                 "mode": "set" if specs else "sequence",
                 "entries": serialisable},
                indent=1,
            )
        )
        # A symmetric T-pose puts two horizontal sticks at shoulder height, and
        # a model told the subject is holding a sword will happily read those
        # sticks AS swords. Measured: arms came back replaced by blades.
        if opt(cfg, "symmetric", False):
            words = f"{ctx.config.get('subject', '')} {ctx.config.get('style', '')}".lower()
            elongated = [w for w in ("sword", "staff", "spear", "bow", "axe", "wand", "rifle")
                         if w in words]
            if elongated:
                print(
                    f"   warning: symmetric T-pose with '{elongated[0]}' in the prompt. "
                    f"Horizontal arms are often drawn as the held object. "
                    f"Consider pose.symmetric: false."
                )

        angles = sorted({e["yaw"] for e in entries})
        print(f"   rig: {rig.describe()}")
        print(
            f"   {len(written)} skeleton(s), "
            f"{'pose set' if specs else 'sequence'}, "
            f"view(s) {', '.join(f'{a:g}°' for a in angles)}"
        )
        return {"skeletons": written, "pose_frames": entries}

    @staticmethod
    def _from_annotations(ctx: Context, cfg: dict) -> list[dict]:
        """Poses taken from annotated references.

        This is what makes annotation more than a note to yourself: the marked
        composition becomes the control image, so a generation reproduces that
        pose and framing with your character in it. Screen-space points cannot
        be projected to other angles, so each annotation yields exactly one
        entry, at the view it was measured at.
        """
        from ..geometry import annotate as ann

        found = ann.gather(ctx.root, ctx.config.get("references") or {})
        if not found:
            raise ValueError(
                "pose.source is 'annotation' but no reference has one. "
                "Annotate at least one image, or use another pose source."
            )
        out = []
        for a in found:
            yaw, _confidence = ann.infer_view(a)
            out.append({"annotation": a, "yaw": yaw})
        print(f"   {len(out)} annotated composition(s)")
        return out

    @staticmethod
    def _views_from_references(ctx: Context) -> list[dict]:
        """One view per supplied reference, at the angle that reference shows."""
        lib = ctx.references()
        refs = lib.identity or lib.pose
        if not refs:
            raise ValueError(
                "pose.views is 'from_references' but no references are set. "
                "Add reference images, or list the views explicitly."
            )
        seen, out = set(), []
        for ref in refs:
            if ref.yaw in seen:
                continue
            seen.add(ref.yaw)
            out.append({"view": ref.yaw})
        print(f"   views taken from {len(out)} reference angle(s)")
        return out

    def _resolve(self, ctx: Context, cfg: dict, wanted: int | None) -> list[dict]:
        source = opt(cfg, "source", "library")
        if not ctx.rig().joints:
            # One placeholder per requested view; there is no pose to resolve.
            return [{}]
        if source == "annotation":
            return self._from_annotations(ctx, cfg)
        if source == "tpose":
            # One reference pose; the character sheet varies the view, not the
            # pose, so a single frame is repeated across the requested views.
            return [rig_lib.tpose(
                ctx.rig(),
                symmetric=bool(opt(cfg, "symmetric", False)),
            )]
        if source == "library":
            return self._from_library(ctx, cfg, wanted)
        if source == "llm":
            return self._from_llm(ctx, cfg, wanted or 6)
        raise ValueError(
            f"pose.source '{source}' is not supported. "
            f"Use 'annotation', 'tpose', 'library' or 'llm'."
        )

    # ------------------------------------------------------------- backends

    def _from_library(self, ctx: Context, cfg: dict, limit: int | None) -> list[dict]:
        pose_name = opt(cfg, "name", "idle")
        path = ctx.root / "poses" / f"{pose_name}.json"
        if not path.exists():
            options = sorted(p.stem for p in (ctx.root / "poses").glob("*.json"))
            raise FileNotFoundError(
                f"no pose '{pose_name}'. Available: {', '.join(options) or '(none)'}. "
                f"Add one in tools/make_poses.py, or set pose.source: llm."
            )

        data = json.loads(path.read_text())
        if data.get("space") != "body":
            raise ValueError(
                f"{path} is in legacy screen space. Re-run tools/make_poses.py."
            )
        rig = ctx.rig()
        base = {k: list(v) for k, v in rig.neutral.items()}
        frames = [{**base, **{k: list(v) for k, v in f.items()}} for f in data["frames"]]
        return frames[:limit] if limit else frames

    def _from_llm(self, ctx: Context, cfg: dict, n_frames: int) -> list[dict]:
        from ..refs.llm import Ollama, generate_pose

        llm_cfg = opt(cfg, "llm", {}) or {}
        action = cfg.get("action") or cfg.get("name") or "idle standing"

        # Generated poses are cached as ordinary library files, so a sequence
        # you like becomes reusable, editable, and reviewable in a diff rather
        # than being re-rolled on every run.
        cache_dir = ctx.root / "poses" / "generated"
        cached = cache_dir / f"{_slug(action)}_{n_frames}f.json"
        if opt(llm_cfg, "cache", True) and cached.exists():
            data = json.loads(cached.read_text())
            print(f"   reusing cached pose {cached.relative_to(ctx.root)}")
            base = {k: list(v) for k, v in ctx.rig().neutral.items()}
            return [{**base, **f} for f in data["frames"]]

        client = Ollama(
            host=opt(llm_cfg, "host", "http://127.0.0.1:11434"),
            model=opt(llm_cfg, "model", "qwen3:4b"),
            keep_alive=opt(llm_cfg, "keep_alive", 0),
        )
        if not client.alive():
            raise RuntimeError(
                "Ollama is not running. Start it with `ollama serve`, or switch "
                "pose.source back to 'library'."
            )

        print(f"   asking {client.model} for '{action}' ({n_frames} frames)")
        frames = generate_pose(
            client, action, n_frames,
            temperature=opt(llm_cfg, "temperature", 0.7),
            attempts=opt(llm_cfg, "attempts", 3),
            tolerance=opt(llm_cfg, "tolerance", 0.3),
        )

        for i, f in enumerate(frames):
            issues = validate_pose(f, opt(llm_cfg, "tolerance", 0.3))
            if issues:
                raise RuntimeError(f"frame {i} failed validation after accept: {issues}")

        if opt(llm_cfg, "cache", True):
            cache_dir.mkdir(parents=True, exist_ok=True)
            cached.write_text(
                json.dumps(
                    {"name": _slug(action), "description": f"LLM: {action}",
                     "space": "body", "generated_by": client.model, "frames": frames},
                    indent=1,
                )
            )
            print(f"   cached -> {cached.relative_to(ctx.root)}")
        return frames
