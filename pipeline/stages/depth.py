"""Optional stage — depth maps to accompany the skeletons."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Any

from ..geometry import props as props_mod
from ..geometry import rigs as rig_lib
from ..geometry.bodyspace import resolve_view
from ..geometry.depthmap import render_depth
from ..generation.stage import Context, Resource, Stage, opt, register


def render_entries(ctx: Context, frames: list[dict], outdir: Path) -> list[Path]:
    cfg = ctx.settings("depth")
    pose_cfg = ctx.settings("pose")

    override = cfg.get("view")
    size = opt(cfg, "size", pose_cfg["size"])
    rig = ctx.need("rig")
    props = props_mod.load(ctx.config.get("props"), root=ctx.root)
    if props and not props_mod.wanted(ctx.config):
        props = []
    if props:
        print(f"   props: {props_mod.describe(props)}")

    if not rig.joints:
        print("   rig-free: no geometry to render depth from")
        return []

    written: list[Path] = []
    for i, entry in enumerate(frames):
        body_pose = entry["pose"]
        if props:
            body_pose = props_mod.pull_second_hand(props, body_pose, rig)
        yaw = resolve_view(override) if override else entry["yaw"]
        img = render_depth(
            body_pose, yaw, size, size,
            near=cfg["near"],
            far=cfg["far"],
            blur=cfg["blur"],
            fill=float(pose_cfg["fill"]),
            build=cfg.get("build"),
            depth_scale=pose_cfg["depth_scale"],
            lateral_scale=pose_cfg["lateral_scale"],
            rig=rig,
            props=props,
        )
        dst = outdir / f"depth_{i:03d}.png"
        img.save(dst)
        written.append(dst)

    return written


@register
class DepthStage(Stage):
    name = "depth"
    resource = Resource.CPU
    gives = frozenset({"depthmaps"})
    needs = frozenset({"pose_frames", "rig"})

    def run(self, ctx: Context, prep: Mapping[str, Any]) -> dict[str, Any]:
        frames: list[dict] = ctx.require("pose_frames")
        written = render_entries(ctx, frames, ctx.stage_dir("depth"))
        print(f"   {len(written)} depth map(s)")
        return {"depthmaps": written}
