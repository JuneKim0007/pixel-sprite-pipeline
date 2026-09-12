# Downloaded art to sprites

Directives for turning scraped pixel art into training sprites. Run every
command from the repository root with `ComfyUI/.venv/bin/python`.

This file is the only source for this pipeline. `training_set/README.md`
describes what is currently in the directory and points here; `AGENTS.md`
carries a pointer and nothing else. Update this file, then those.

## Stages

    _source/  -> sorted/<bucket>/  -> keyed/<bucket>/  -> sprite/
                 (sort by hand)      (denoise_training)  (pixelize_training)

Each stage is its own directory because they were flat until 2026-09-12 and
keyed output sat in the folder the tools read as input, so a default re-run
denoised twice. `_scratch/` holds A/B sweeps and is never an input.

Sources are never modified. Raw downloads live in `training_set/_source/`.
`training_set/index.csv` maps every generated name to the file it came from.

## Work one image at a time

Do NOT tune a constant, re-run all 35, and read the mean. A batch hides the
one figure that lost an arm. Process one image, look at it against its own
input at integer zoom, then move on. Re-run the batch only to confirm a
change you have already verified singly.

## Verify by looking, always

Kept-fraction cannot tell "backdrop removed" from "character removed".
Render on magenta, at NEAREST and integer zoom - a smooth filter grades the
viewer, not the sprite.

Reject a result that shows magenta through clothing, that is reduced to
fragments, or that has lost a limb, a weapon or a held object. Compare thin
features - sleeves, straps, blade tips - against the input directly. A 2.5x
reduction turns a 3px sleeve into 1px, and an alpha threshold then deletes it.

## Sorting

Measure the SUBJECT, never the file. Use `pipeline.geometry.framing.measure`.
A 736x1104 file whose figure is 485x1014 is a 2.09 aspect subject; the file's
1.50 buckets it wrongly.

Be generous. When the native detail is ambiguous, choose the larger canvas:
too much canvas wastes pixels, too little destroys them irreversibly.

Assume the palette is gone. 34 of 35 files are JPEG or WebP carrying
1,600-7,400 colours where the art has tens. Re-download as PNG wherever the
source still offers it; it is worth more than any sorting or keying.

Do NOT judge "is this pixel art" by colour count or edge softness. On
compressed sources both measure the compression. Tried and discarded
2026-09-11.

## Denoising

    ComfyUI/.venv/bin/python tools/denoise_training.py

Order of operations: sample the frame's border ring, quantise to 4 bits so
JPEG noise collapses, keep colours holding >= 4% of the ring, flood the UNION
of them from the frame edge, then drop components under 4% of the largest.

Rules, each bought with a measured failure:

- Flood the union; never key a colour globally. Global keying removes white
  hair with the white border. Connectivity keeps an enclosed white shirt and
  discards a white frame.
- Judge a later round on the round's total, not on one colour. A decorated
  panel is several tones and no single tone is a panel.
- Judge "did the backdrop survive" against the figure's measured area, never
  a flat kept fraction. A figure that fills its frame keeps 70% legitimately.
- Raise the tolerance to cross an anti-aliased seam. Do NOT bridge it with a
  dilation: 2px also crosses a character's outline and eats the figure.
- Never use `binary_closing` for a reachability test. It erodes with
  `border_value=0`, strips the mask's contact with the frame edge, and leaves
  nothing reachable.

## Sprites

    ComfyUI/.venv/bin/python tools/pixelize_training.py

Reduce first, then quantise. Measured across 35: reduce-then-quantise won on
32. Quantising first picks a palette from detail about to be discarded, then
the reduction averages palette entries into colours not in the palette and
must be fitted twice, compounding error.

Premultiply by alpha before resizing and unpremultiply after, or every edge
pixel blends with the transparent black beside it and leaves a dark fringe.
Threshold alpha to 0 or 255: a sprite edge is in or out.

Do NOT choose a resampling filter by error against a LANCZOS reference. That
compares LANCZOS with itself and always elects it. Judge on soft-alpha count
and on the image.

## Target style

The look being aimed at, in the terms it is judged by:

- **Clear.** Outlines and silhouette edges stay hard. Reduction must not
  soften a boundary into a ramp of intermediate colours.
- **Clustered.** Flat regions of solid colour with visible separation between
  them. Not gradients, not dithered blends.
- **Fidelity.** The result still reads as the same character: hue identity,
  limb count, held objects, and the shapes that make it recognisable.

Consequences to hold to:

- `median`, settled 2026-09-11. `mean` softens an outline into a ramp, and
  `salient` - which looked right, because it keeps the extreme where a block
  has contrast - promotes what keying and JPEG leave behind: on GAME KING it
  spent 1 of 10 entries on the backdrop blue it had just removed.
- Palette target is 10 colours, measured on opaque pixels only.
- Contrast 1.12. From 1.10 to 1.20 clarity rises 6% and fidelity error 35%.
- Brightness per image, 0 to +20, from the figure's own luma. The set spans
  70 to 195; one fixed lift either muddies the dark or blows out the pastel.
- Keep a component that is small but touching. Parts sit 1-11px from the
  silhouette and captions sit 111-130px clear, so a distance transform
  separates them where a bounding-box gap cannot - that gap lost a crown and
  a pair of boots.
- Contrast before quantising pushes midtones toward the ends of the ramp and
  makes the palette spend its entries on separation rather than on blend.
  Apply it before the palette, never after - after, it just moves colours off
  the palette again.

## Open

- Inner backgrounds enclosed by the figure are not removed. The keyer clears
  only backdrop reachable from the frame edge, which is the same rule that
  protects a white shirt. Candidate fix: clear enclosed holes whose colour
  matches an original ring colour. Untried.
- Composite illustrations - figure plus captioned panel plus separate object -
  are not solved. `ice Person 2` keeps its card, caption and mushroom.
