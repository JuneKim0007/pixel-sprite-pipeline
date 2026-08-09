"""Stage 5 — assemble the sprite sheet and record what produced it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from ..stage import Context, Resource, Stage, opt, register


@register
class ExportStage(Stage):
    name = "export"
    resource = Resource.CPU
    requires = frozenset({"pixel_frames"})
    produces = frozenset({"sheet"})

    # Extra stage folders worth joining into their own sheets. A character
    # sheet is only half useful without the rigs and depth maps that produced
    # it, and joining costs a paste loop.
    ALSO_JOIN = ("pose", "depth", "frames")

    def run(self, ctx: Context) -> dict[str, Any]:
        cfg = ctx.stage_config("export")
        frames: list[Path] = ctx.require("pixel_frames")
        outdir = ctx.stage_dir("export")

        images = [Image.open(p).convert("RGBA") for p in frames]
        cell_w = max(im.width for im in images)
        cell_h = max(im.height for im in images)

        columns = cfg.get("columns") or len(images)
        rows = (len(images) + columns - 1) // columns

        sheet = Image.new("RGBA", (cell_w * columns, cell_h * rows), (0, 0, 0, 0))
        for i, im in enumerate(images):
            col, row = i % columns, i // columns
            # Centre within the cell so frames of differing crop still line up.
            sheet.paste(
                im,
                (col * cell_w + (cell_w - im.width) // 2,
                 row * cell_h + (cell_h - im.height) // 2),
            )

        scale = opt(cfg, "scale", 1)
        if scale > 1:
            sheet = sheet.resize(
                (sheet.width * scale, sheet.height * scale), Image.Resampling.NEAREST
            )

        dst = outdir / "sheet.png"
        sheet.save(dst)

        # Per-view files already exist in each stage folder; these are the
        # joined companions, so both forms are always available.
        for name in self.ALSO_JOIN:
            found = sorted(ctx.outdir.glob(f"*_{name}"))
            if not found:
                continue
            pngs = sorted(found[0].glob("*.png"))
            if len(pngs) < 2:
                continue
            ims = [Image.open(p).convert("RGBA") for p in pngs]
            cw = max(i.width for i in ims)
            ch = max(i.height for i in ims)
            joined = Image.new("RGBA", (cw * len(ims), ch), (0, 0, 0, 0))
            for i, im in enumerate(ims):
                joined.paste(im, (i * cw + (cw - im.width) // 2, (ch - im.height) // 2))
            joined.save(outdir / f"sheet_{name}.png")

        # A sheet is useless to an engine without its cell geometry.
        (outdir / "sheet.json").write_text(
            json.dumps(
                {
                    "run_id": ctx.run_id,
                    "frames": len(images),
                    "columns": columns,
                    "rows": rows,
                    "cell": {"width": cell_w * scale, "height": cell_h * scale},
                    "source_frames": [p.name for p in frames],
                },
                indent=2,
            )
        )

        print(f"   sheet {sheet.width}x{sheet.height} ({columns}x{rows} cells) -> {dst.name}")
        return {"sheet": dst}
