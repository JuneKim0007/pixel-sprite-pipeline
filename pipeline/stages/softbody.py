"""Optional stage — apply spring-driven secondary motion to the frames."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Any

import numpy as np
from PIL import Image

from ..geometry.softbody import SoftNode, build_tracks, describe, warp_frame
from ..generation.stage import Context, Resource, Stage, opt, register


@register
class SoftBodyStage(Stage):
    name = "softbody"
    DEFAULTS = {"nodes": []}
    resource = Resource.CPU
    needs = frozenset({"frames", "pose_frames"})
    gives = frozenset({"soft_frames"})

    def run(self, ctx: Context, prep: Mapping[str, Any]) -> dict[str, Any]:
        cfg = ctx.settings("softbody")
        pose_cfg = ctx.settings("pose")
        frames: list[Path] = ctx.require("frames")
        entries: list[dict] = ctx.require("pose_frames")
        outdir = ctx.stage_dir("softbody")

        specs = cfg["nodes"] or []
        nodes = [SoftNode.from_config(s) for s in specs]

        tracks = build_tracks(
            nodes, entries,
            fps=cfg["fps"],
            loop=cfg["loop"],
            preroll=cfg["preroll_cycles"],
            depth_scale=opt(pose_cfg, "depth_scale", 1.0),
            lateral_scale=opt(pose_cfg, "lateral_scale", 1.0),
        )
        print(f"   {describe(tracks)}")

        written: list[Path] = []
        for i, src in enumerate(frames):
            img = np.asarray(Image.open(src).convert("RGB"))
            out = warp_frame(img, tracks, i) if tracks else img
            dst = outdir / f"{src.stem}_soft.png"
            Image.fromarray(out).save(dst)
            written.append(dst)

        print(f"   warped {len(written)} frame(s)")
        return {"soft_frames": written}
