# De-noising the training set

Directives. Run from the repository root.

## Run it

    ComfyUI/.venv/bin/python tools/denoise_training.py

- Read from `training_set/128x128/` and `training_set/256x256/`.
- Write RGBA PNGs to `training_set/temp/` named `<bucket>_<NN>.png`.
- Write `training_set/temp/index.csv` mapping each name to its source and kept fraction.
- Never modify a source file. Delete `training_set/temp/` and re-run to redo.

Flags: `--src`, `--out`, `--tolerance`, `--keep-parts`.

## Verify before trusting the output

Always render a contact sheet on magenta and look at it. Kept-fraction alone
does not distinguish "backdrop removed" from "character removed".

    ComfyUI/.venv/bin/python - <<'PY'
    from pathlib import Path
    from PIL import Image
    files = sorted(Path("training_set/temp").glob("*.png"))
    CELL, COLS = 200, 9
    sheet = Image.new("RGB", (COLS*CELL, ((len(files)+COLS-1)//COLS)*CELL), (255,0,255))
    for i, p in enumerate(files):
        with Image.open(p) as im:
            im = im.convert("RGBA"); im.thumbnail((CELL-8, CELL-8))
            c = Image.new("RGBA", (CELL, CELL), (255,0,255,255))
            c.alpha_composite(im, ((CELL-im.size[0])//2, (CELL-im.size[1])//2))
        sheet.paste(c.convert("RGB"), ((i%COLS)*CELL, (i//COLS)*CELL))
    sheet.save("/tmp/sheet.png")
    PY

Reject the run if any figure shows magenta through clothing or is reduced to
fragments. Both mean a backdrop colour matched the character.

## What the tool does, in order

1. Sample the frame's border ring. Quantise to 4 bits per channel so JPEG
   noise collapses. Keep colours holding at least 4% of the ring, up to 6.
2. Build one mask of every pixel matching any of those colours.
3. Clear only the part of that mask reachable from the frame's edge.
4. Repeat up to 2 more times, sampling the boundary of what is still opaque.
   Accept a round only when it clears at least 20% of the frame.
5. Drop connected components smaller than 4% of the largest.

## Rules for changing it

- Flood the UNION of backdrop colours; never key a colour globally. Global
  keying removes white hair along with a white border. Connectivity is what
  keeps an enclosed white shirt and discards a white frame.
- Judge a later round on the round's total, not on one colour. A decorated
  panel is several tones and no single tone is a panel.
- Judge "did the backdrop survive" against the figure's measured area
  (`pipeline.geometry.framing.measure`), never against a flat kept fraction.
  A figure that fills its frame keeps 70% legitimately.
- Raise `TOLERANCES` to cross an anti-aliased seam. Do NOT bridge the seam
  with a dilation: a 2px dilation also crosses a character's outline and eats
  the figure. Measured, both ways, 2026-09-11.
- Never use `binary_closing` for a reachability test. It erodes with
  `border_value=0`, strips the mask's contact with the frame edge, and leaves
  nothing reachable.
- Re-render the contact sheet after ANY constant changes. Lowering
  `MIN_PANEL` from 0.20 to 0.14 fixed one image and partly ate three.

## Making sprites

    ComfyUI/.venv/bin/python tools/pixelize_training.py

- Read the cleaned RGBA PNGs from `training_set/128x128/` and `256x256/`.
- Write sprites to `training_set/sprites/`, same names, plus `sprites.csv`.
- 12 colours, measured on opaque pixels only. Raise with `--colours`.

Canvas is chosen from the figure's aspect, not the file's:

| bucket | subject aspect | canvas |
|---|---|---|
| 256 | any | 256x256 |
| 128 | <= 1.15 | 128x128 |
| 128 | <= 1.45 | 128x160 |
| 128 | > 1.45 | 128x192 |

### Reduce first, then quantise

Measured 2026-09-11 across all 35: reduce-then-quantise beat
quantise-then-reduce on 32. Both hit 12 colours exactly, so the difference is
fidelity alone (mean weighted-RGB error 9.63 against 10.44).

Quantising first picks a palette from detail that is about to be discarded,
and the reduction then averages palette entries into colours that are not in
the palette - so it must be fitted a second time, and the error compounds.
Keep `--order reduce_first` unless something changes.

### Resizing rules

- Premultiply by alpha before resizing, and unpremultiply after. Resizing
  RGBA straight blends every edge pixel with the transparent black beside it
  and leaves a dark fringe.
- Threshold alpha to 0 or 255 afterwards. A sprite edge is in or out; a
  half-transparent pixel is a colour the palette never chose. Measured: BOX
  without thresholding left 827 soft pixels per image, LANCZOS 2,898.
- Do NOT pick the filter by error against a LANCZOS reference. That metric
  compares LANCZOS with itself and will always choose it. Judge on soft-alpha
  count and on the contact sheet.

### Verify

Render the sheet with NEAREST at integer zoom, never a smooth filter, or you
are grading the viewer instead of the sprite. Then check no two palette
entries are within ~15 sum-abs-RGB and no entry holds under ~0.2% of pixels;
either means a colour was spent on nothing.

## Known limit

A composite illustration - figure plus a captioned panel plus a separate
object - is not solved. `ice Person 2` keeps its blue card, its AGARIC
caption and its mushroom. Crop these by hand.
