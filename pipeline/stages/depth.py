"""Optional stage — depth maps to accompany the skeletons.

A 2D skeleton cannot express viewing angle: three-quarter-rear and full-rear
project to almost the same keypoints, because horizontal spread barely changes
between them. Depth is the missing channel, and because poses are authored in
body space it is computed rather than estimated.

Stacks with the pose ControlNet — the Union model handles both `openpose` and
`depth`, so this costs no extra model, only a second conditioning pass.

Drop `depth` from `pipeline.stages` to disable it; `frames` treats the depth
maps as optional and simply won't use them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..geometry import props as props_mod
from ..geometry import rigs as rig_lib
from ..geometry.bodyspace import resolve_view
from ..geometry.depthmap import render_depth
from ..generation.stage import Context, Resource, Stage, opt, register


def render_entries(ctx: Context, frames: list[dict], outdir: Path) -> list[Path]:
    """Draw one depth map per pose entry; the only renderer for depth images.

    Shared with the rig editor, whose copy dropped fill, build, rig and props.
    """
    cfg = ctx.stage_config("depth")
    pose_cfg = ctx.stage_config("pose")

    # Each entry carries its own yaw, so a pose set whose entries use
    # different viewing angles gets depth maps that match each one.
    override = cfg.get("view")
    size = opt(cfg, "size", opt(pose_cfg, "size", 1024))
    rig = ctx.rig()
    props = props_mod.load(ctx.config.get("props"), root=ctx.root)
    if props and not props_mod.wanted(ctx):
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
            # A two-handed weapon needs the off hand brought to the shaft.
            body_pose = props_mod.pull_second_hand(props, body_pose, rig)
        yaw = resolve_view(override) if override else entry["yaw"]
        img = render_depth(
            body_pose, yaw, size, size,
            near=opt(cfg, "near", 255),
            far=opt(cfg, "far", 60),
            blur=opt(cfg, "blur", 6.0),
            fill=float(opt(pose_cfg, "fill", 0.0) or 0.0),
            build=opt(cfg, "build", None),
            depth_scale=opt(pose_cfg, "depth_scale", 1.0),
            lateral_scale=opt(pose_cfg, "lateral_scale", 1.0),
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
    requires = frozenset({"pose_frames"})
    produces = frozenset({"depthmaps"})

    def run(self, ctx: Context) -> dict[str, Any]:
        frames: list[dict] = ctx.require("pose_frames")
        written = render_entries(ctx, frames, ctx.stage_dir("depth"))
        print(f"   {len(written)} depth map(s)")
        return {"depthmaps": written}
