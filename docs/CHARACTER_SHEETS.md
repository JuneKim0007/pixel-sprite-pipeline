# Character sheets

The character sheets and the four views cut from each one. Read this before
touching `characters/`, `tools/cut_sheet.py`, or anything that reads either.

## Where things live, and why the split matters

| directory | holds | who reads it |
|---|---|---|
| `characters/` | character sheets and their cut views | the sweep, for scoring; the pipeline, when a run asks for an identity reference |
| `training_set/` | material the LoRA trains on | training only |
| `library/refs/` | anything a run may be handed as a reference | the pipeline |

All three are gitignored. They are separate because they answer different
questions, and because a file in `library/refs/` can reach the model by
accident — that is what the directory is for. A character sheet that is only
ground truth for a score must not sit there.

`characters/<char>/` contains:

```
_source_sheet.png   the sheet as downloaded, never modified
front.png
side.png            the left side
side_right.png      the right side, or a copy of side.png when the sheet draws one
rear.png
```

## The contract every cut view satisfies

1. **Square.** IPAdapter's `CLIPImageProcessor` centre-crops anything else. A
   cutout at aspect 0.27 loses 73% of the figure to that crop, head first.
2. **The figure spans 0.82 of the frame** (`SHARE` in `tools/cut_sheet.py`),
   measured on its long axis. The same on every view of every character, so
   references are comparable to each other.
3. **Centred**, with the padding split evenly.
4. **Nothing touches an edge.** A reference whose subject runs to the frame
   edge teaches the model to draw one that does; all eight characters were
   generated running off the top and bottom of a 1024 canvas because of it.
5. **The aspect ratio is preserved.** Padding, never stretching. A side view is
   mostly padding and that is correct.
6. **No captions.** Sheets label their views ("FRONT", "LEFT SIDE"); a blob
   shorter than `CAPTION` of the sheet height is not part of a figure.

Verify with `pipeline.geometry.framing.measure`: `fill` must be ~0.82 and
`clipped` must be empty.

## How the cut works, and the mistake not to repeat

Finding the figures and finding one figure's extent are two different jobs, and
they need different evidence.

- **Which figures exist** comes from column detection (`_columns`): scan for
  columns whose ink spans most of the shared band. This is reliable — it finds
  all four figures on all eight sheets.
- **How far one figure reaches** comes from connected components (`_blobs`):
  the figure's own parts, unioned. A column only says *which* parts.

The earlier version measured a margin off the column instead — column width
× 0.16, then trimmed back to ink. On char4 the column ran x=159–349, so the
window opened at x=129, while her hair began at **x=64**. The hair was cut
before the trim ever saw it, and because the result was then tight against its
own box it looked deliberate. Four of eight characters lost hair or a weapon
this way.

Components alone are not enough either: on char1 only one figure survives the
shape filter, and on char7 the boxes nest. Use both.

## Re-cutting

One sheet at a time. A batch pass shares a vertical band and neighbour
midpoints across four figures, and those are the decisions that go wrong.

```
./ComfyUI/.venv/bin/python tools/cut_sheet.py characters/char4/_source_sheet.png characters/char4
```

It prints the figure box and the square it was padded to. Check the output
against the contract above before trusting it.

## If you change `SHARE`

Every reference changes with it, so scores computed before the change are not
comparable to scores after. Re-cut all eight and say so in the commit.
