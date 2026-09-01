"""What the four CPU stage bodies decide.

depth, softbody, palette and export need no GPU and no ComfyUI — they compute
from files on disk — and none of them had a test executing `run`. That gap was
recorded as a GPU problem in docs/OPEN.md section 1; it was never one.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from pipeline.generation.stage import get
from pipeline.shared.errors import NotFound


# -------------------------------------------------------------------- depth

def test_depth_writes_one_map_per_pose_entry(stage_ctx, pose_entries):
    ctx = stage_ctx()
    ctx.artifacts["pose_frames"] = pose_entries(3)
    out = get("depth")().run(ctx, {})

    assert len(out["depthmaps"]) == 3
    assert all(p.exists() for p in out["depthmaps"])


def test_depth_refuses_to_run_without_poses(stage_ctx):
    with pytest.raises(NotFound, match="pose_frames"):
        get("depth")().run(stage_ctx(), {})


def test_depth_maps_differ_between_views(stage_ctx, pose_entries):
    """A depth map that ignored yaw would make every view identical."""
    ctx = stage_ctx()
    ctx.artifacts["pose_frames"] = pose_entries(2)
    out = get("depth")().run(ctx, {})

    a, b = (np.asarray(Image.open(p).convert("L")) for p in out["depthmaps"])
    assert not np.array_equal(a, b), "front and side produced the same map"


# ----------------------------------------------------------------- softbody

def test_softbody_passes_frames_through_when_no_nodes_are_configured(stage_ctx, frames, pose_entries, png):
    ctx = stage_ctx()
    ctx.artifacts["frames"] = frames(2)
    ctx.artifacts["pose_frames"] = pose_entries(2)
    out = get("softbody")().run(ctx, {})

    assert len(out["soft_frames"]) == 2
    for src, dst in zip(ctx.artifacts["frames"], out["soft_frames"]):
        assert dst.name == f"{src.stem}_soft.png"
        assert np.array_equal(np.asarray(Image.open(src).convert("RGB")),
                              np.asarray(Image.open(dst).convert("RGB"))), \
            "with no soft nodes the pixels must survive untouched"


def test_softbody_writes_a_frame_per_input_with_a_node_configured(stage_ctx, frames, pose_entries, png):
    """The warp itself is not asserted: it needs the anchor to move faster than
    the spring tracks it, and at the default stiffness a two-frame pose that
    does that could not be constructed here. What is asserted is the shape."""
    ctx = stage_ctx(softbody={"nodes": [{"name": "belly", "anchor": "neck",
                                       "max_displacement": 0.3,
                                       "influence": 1.0}]})
    ctx.artifacts["frames"] = frames(2)
    ctx.artifacts["pose_frames"] = pose_entries(2)
    out = get("softbody")().run(ctx, {})

    assert [p.name for p in out["soft_frames"]] == ["frame_000_soft.png",
                                                    "frame_001_soft.png"]


def test_softbody_names_the_joint_a_node_cannot_anchor_to(stage_ctx, frames, pose_entries):
    ctx = stage_ctx(softbody={"nodes": [{"name": "belly", "anchor": "tentacle"}]})
    ctx.artifacts["frames"] = frames(1)
    ctx.artifacts["pose_frames"] = pose_entries(1)

    with pytest.raises(NotFound) as caught:
        get("softbody")().run(ctx, {})
    assert "tentacle" in str(caught.value)
    assert "neck" in caught.value.hint, "the hint lists the joints that do exist"


def test_softbody_names_an_unknown_node_key(stage_ctx, tmp_path, frames, pose_entries, png):
    ctx = stage_ctx(softbody={"nodes": [{"name": "belly", "nope": 1}]})
    ctx.artifacts["frames"] = frames(1)
    ctx.artifacts["pose_frames"] = pose_entries(1)

    with pytest.raises(Exception, match="soft node"):
        get("softbody")().run(ctx, {})


# ------------------------------------------------------------------ palette

@pytest.fixture
def palette_ctx(stage_ctx, frames, png, tmp_path):
    """A Context the palette stage can run against: frames and an anchor to measure."""
    def build(n=2, **config):
        ctx = stage_ctx(**config)
        ctx.artifacts["frames"] = frames(n, (32, 32))
        ctx.artifacts["canonical"] = png(tmp_path / "in" / "canonical.png", (32, 32))
        return ctx
    return build


def test_palette_writes_a_hex_file_and_one_image_per_frame(palette_ctx, png):
    ctx = palette_ctx(2)
    stage = get("palette")()
    out = stage.run(ctx, stage.prepare(ctx))

    assert out["palette"].name == "palette.hex"
    assert out["palette"].exists()
    assert len(out["pixel_frames"]) == 2
    assert all(p.exists() and p.name.endswith("_px.png") for p in out["pixel_frames"])


def test_palette_records_the_run_it_came_from(palette_ctx):
    ctx = palette_ctx(1)
    stage = get("palette")()
    out = stage.run(ctx, stage.prepare(ctx))

    assert "testrun" in out["palette"].read_text()


def test_palette_prefers_soft_frames_when_softbody_ran(tmp_path, palette_ctx, png):
    ctx = palette_ctx(2)
    soft = [png(tmp_path / "soft" / f"s_{i}.png", (32, 32)) for i in range(3)]
    ctx.artifacts["soft_frames"] = soft
    stage = get("palette")()
    out = stage.run(ctx, stage.prepare(ctx))

    assert len(out["pixel_frames"]) == 3, "the softbody output is what gets pixelized"
    assert all("s_" in p.name for p in out["pixel_frames"])


def test_palette_holds_every_frame_to_the_same_colours(palette_ctx, frames):
    """Fixing the palette is the point: frames must not drift colour apart."""
    ctx = palette_ctx(2)
    stage = get("palette")()
    out = stage.run(ctx, stage.prepare(ctx))

    declared = {line.strip() for line in out["palette"].read_text().splitlines()
                if line.strip() and not line.startswith("//")}
    for p in out["pixel_frames"]:
        arr = np.asarray(Image.open(p).convert("RGB")).reshape(-1, 3)
        used = {f"{r:02X}{g:02X}{b:02X}" for r, g, b in np.unique(arr, axis=0)}
        assert used <= declared, f"{p.name} used a colour the palette does not hold"


# ------------------------------------------------------------------- export

def test_export_joins_the_frames_into_one_sheet(stage_ctx, frames):
    ctx = stage_ctx()
    ctx.artifacts["pixel_frames"] = frames(3, (8, 8))
    out = get("export")().run(ctx, {})

    assert out["sheet"].exists()
    sheet = Image.open(out["sheet"])
    assert sheet.size == (24, 8), "three 8px cells in one row"


def test_export_wraps_into_rows_at_the_configured_column_count(stage_ctx, frames):
    ctx = stage_ctx(export={"columns": 2})
    ctx.artifacts["pixel_frames"] = frames(3, (8, 8))
    out = get("export")().run(ctx, {})

    assert Image.open(out["sheet"]).size == (16, 16), "2 columns, 2 rows"


def test_export_sizes_every_cell_to_the_largest_frame(stage_ctx, tmp_path, png):
    ctx = stage_ctx()
    ctx.artifacts["pixel_frames"] = [
        png(tmp_path / "in" / "a.png", (8, 8)),
        png(tmp_path / "in" / "b.png", (12, 10)),
    ]
    out = get("export")().run(ctx, {})

    assert Image.open(out["sheet"]).size == (24, 10), "cells are 12x10"


def test_export_refuses_to_run_without_pixel_frames(stage_ctx):
    with pytest.raises(NotFound, match="pixel_frames"):
        get("export")().run(stage_ctx(), {})
