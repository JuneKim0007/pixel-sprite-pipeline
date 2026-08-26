"""Stage 1 — produce skeleton control images."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping, Any

from ..geometry import props as props_mod
from ..geometry import rigs as rig_lib
from ..geometry.bodyspace import frame_scale, project, resolve_view, validate_pose
from ..geometry.openpose import render
from ..generation.stage import Context, Resource, Stage, opt, register
from ..shared.errors import Invalid, NotFound


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48] or "pose"


def render_entries(ctx: Context, entries: list[dict], outdir: Path) -> list[Path]:
    cfg = ctx.stage_config("pose")
    size = cfg["size"]
    rig = ctx.need("rig")

    props = props_mod.load(ctx.config.get("props"), root=ctx.root)
    if props and not props_mod.wanted(ctx):
        props = []

    fill = float(cfg["fill"])
    thickness = cfg.get("thickness")
    written: list[Path] = []

    for i, entry in enumerate(entries):
        dst = outdir / f"skeleton_{i:03d}.png"
        annotation = entry.get("annotation")
        if annotation is not None:
            from ..geometry import annotate as ann

            ann.render(annotation, size, size).save(dst)
            written.append(dst)
            entry["pose"] = {}
            entry["from_annotation"] = str(annotation.image)
            entry["crop"] = ann.infer_crop(annotation)
            continue

        if props:
            entry["pose"] = props_mod.pull_second_hand(props, entry["pose"], rig)
        keypoints = project(
            entry["pose"], entry["yaw"],
            depth_scale=cfg["depth_scale"],
            lateral_scale=cfg["lateral_scale"],
            fill=fill,
            rig=rig,
        )
        grow = frame_scale(entry["pose"], fill)
        render(keypoints, size, size, rig=rig,
               thickness=(thickness * grow) if thickness else None).save(dst)
        written.append(dst)

    return written


@register
class PoseStage(Stage):
    name = "pose"
    resource = Resource.CPU
    DEFAULTS = {
        "size": 1024, "fill": 0.0, "depth_scale": 1.0, "lateral_scale": 1.0,
        "views": "", "view": "side", "source": "library", "symmetric": False,
        "name": "idle", "llm": {},
    }
    produces = frozenset({"skeletons", "pose_frames"})
    needs = frozenset({'references', 'rig', 'rig_record'})

    def run(self, ctx: Context, prep: Mapping[str, Any]) -> dict[str, Any]:
        cfg = ctx.stage_config("pose")
        size = cfg["size"]
        outdir = ctx.stage_dir("pose")
        rig = ctx.need("rig")

        specs = cfg.get("set")
        if specs == "from_references" or cfg["views"] == "from_references":
            specs = self._views_from_references(ctx)
        entries: list[dict[str, Any]] = []

        if specs:
            for i, spec in enumerate(specs):
                if not isinstance(spec, dict):
                    raise Invalid(f"pose.set[{i}] must be a mapping", field="set")
                merged = {**cfg, **spec}
                merged.pop("set", None)
                pick = spec.get("frame")
                got = self._resolve(
                    ctx, merged, wanted=None if pick is not None else spec.get("frames", 1)
                )
                if pick is not None:
                    if not 0 <= pick < len(got):
                        raise Invalid(
                            f"pose.set[{i}].frame={pick} but that pose has "
                            f"{len(got)} frame(s)",
                            field="frame",
                        )
                    got = [got[pick]]
                yaw = resolve_view(merged["view"])
                entries += [{"pose": p, "yaw": yaw, "spec": i} for p in got]
        else:
            got = self._resolve(ctx, cfg, wanted=cfg.get("frames"))
            if got and isinstance(got[0], dict) and "annotation" in got[0]:
                entries = [{**g, "spec": 0} for g in got]
            else:
                yaw = resolve_view(cfg["view"])
                entries = [{"pose": p, "yaw": yaw, "spec": 0} for p in got]

        written: list[Path] = []
        if not rig.joints:
            (outdir / "pose.json").write_text(
                json.dumps({"source": "none", "rig": rig.name, "mode": "rig_free",
                            "entries": entries}, indent=1))
            print(f"   rig-free: {len(entries)} view(s), steered by prompt")
            return {"skeletons": [], "pose_frames": entries}

        written = render_entries(ctx, entries, outdir)

        serialisable = [
            {k: v for k, v in e.items() if k != "annotation"} for e in entries
        ]
        (outdir / "pose.json").write_text(
            json.dumps(
                {"source": cfg["source"],
                 "rig": rig.name,
                 "rig_choice": ctx.need("rig_record"),
                 "skeleton_control": rig.skeleton_control,
                 "mode": "set" if specs else "sequence",
                 "entries": serialisable},
                indent=1,
            )
        )
        if cfg["symmetric"]:
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
        from ..geometry import annotate as ann

        found = ann.gather(ctx.need("references"))
        if not found:
            raise Invalid(
                "pose.source is 'annotation' but no reference has one. "
                "Annotate at least one image, or use another pose source.",
                field="source",
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
        lib = ctx.need("references")
        refs = lib.identity or lib.pose
        if not refs:
            raise Invalid(
                "pose.views is 'from_references' but no references are set. "
                "Add reference images, or list the views explicitly.",
                field="views",
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
        source = cfg["source"]
        if not ctx.need("rig").joints:
            return [{}]
        if source == "annotation":
            return self._from_annotations(ctx, cfg)
        if source == "tpose":
            return [rig_lib.tpose(
                ctx.need("rig"),
                symmetric=bool(cfg["symmetric"]),
            )]
        if source == "library":
            return self._from_library(ctx, cfg, wanted)
        if source == "llm":
            return self._from_llm(ctx, cfg, wanted or 6)
        raise NotFound("pose source", source,
                       available=["annotation", "tpose", "library", "llm"])


    def _from_library(self, ctx: Context, cfg: dict, limit: int | None) -> list[dict]:
        from ..looks import poses as pose_lib

        pose_name = cfg["name"]
        data = pose_lib.load(ctx.root, pose_name)
        if data.get("space") != "body":
            raise Invalid(
                f"pose '{pose_name}' is in legacy screen space.",
                field="name",
                hint="re-run tools/make_poses.py",
            )
        rig = ctx.need("rig")
        base = {k: list(v) for k, v in rig.neutral.items()}
        frames = [{**base, **{k: list(v) for k, v in f.items()}} for f in data["frames"]]
        return frames[:limit] if limit else frames

    def _from_llm(self, ctx: Context, cfg: dict, n_frames: int) -> list[dict]:
        from ..refs.llm import LLMError, Ollama, generate_pose

        llm_cfg = cfg["llm"]
        action = cfg.get("action") or cfg.get("name") or "idle standing"

        cache_dir = ctx.root / "poses" / "generated"
        cached = cache_dir / f"{_slug(action)}_{n_frames}f.json"
        if opt(llm_cfg, "cache", True) and cached.exists():
            data = json.loads(cached.read_text())
            print(f"   reusing cached pose {cached.relative_to(ctx.root)}")
            base = {k: list(v) for k, v in ctx.need("rig").neutral.items()}
            return [{**base, **f} for f in data["frames"]]

        client = Ollama(
            host=opt(llm_cfg, "host", "http://127.0.0.1:11434"),
            model=opt(llm_cfg, "model", "qwen3:4b"),
            keep_alive=opt(llm_cfg, "keep_alive", 0),
        )
        if not client.alive():
            raise LLMError(
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
                raise RuntimeError(
                    # not-a-message: generate_pose() only returns frames once
                    # every one of them passed this exact validate_pose(tolerance)
                    # check with no issues, so a failure here means a frame was
                    # mutated (or the tolerance changed) after acceptance — an
                    # invariant break, not something the caller did wrong.
                    f"frame {i} failed validation after accept: {issues}"
                )

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
