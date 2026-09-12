# Training a style LoRA from training_set/sprite

Every number here was measured on this set, on 2026-09-12. Re-measure before
trusting one against a different set.

## What the set is

35 sprites in `training_set/sprite/`, named `small_NN` / `large_NN`. Each is
12 colours - 10 free plus pure black and white where the art uses them -
seated on a canvas between 64x128 and 256x384, with hard alpha.

They came from 35 downloads by roughly 35 different artists, one view each.

    _source/  ->  sorted/{small,large}/  ->  keyed/  ->  sprite/
                  (by hand)                 (denoise)   (pixelize)

## Does it pass the project's own gate

    ComfyUI/.venv/bin/python -c "
    import sys; sys.path.insert(0,'.')
    from pathlib import Path
    from pipeline.looks import training
    print(training.assess(training.inspect(sorted(Path('training_set/sprite').glob('*.png')))))"

Result on 2026-09-12: `ready=True`, no problems, bands `{1px: 35}`.

It did NOT pass before pixelizing. The keyed images measured
`{1px:15, 2-3px:9, 4-6px:1, 8px+:10}` - an 8x spread on the one property
`training.py` says must not vary. Reducing each image by its OWN block size
rather than by one chosen to hit a canvas is what collapsed that: every
sprite now sits on its native lattice, so one pixel means one feature in all
35. That was a side effect of fixing muddiness, not the goal.

## What it can and cannot train

| use | verdict |
|---|---|
| style LoRA | yes - this is the one |
| character LoRA | no. Needs ~20 views of ONE subject; there are 35 subjects with one view each |
| `references.identity` | no. `pick()` chooses one reference per frame by yaw, so identity needs the same character at several angles |
| `references.style` exemplars | yes, and free - drop 2-4 in `library/styles/<name>/context/exemplars/`, auto-discovered, no training. Only two are ever used, at weight <= 0.6 |
| `references.pose` | no. Only the `.rig.json` sidecar is read; none exists |

## What a style LoRA touches

`models.style_lora` is loaded in `comfy.base_graph`, and three stages call
that: `canonical.py:140`, `frames.py:165` and `pixelise.py:87`. So it is not
a frames-only dial - it changes the anchor, every frame, and the re-render
pass together. That is the point of a style LoRA, and it is also why a bad
one is expensive: it moves the identity anchor the frames are then matched
against.

It stacks ON TOP of the pixel LoRA rather than replacing it. `base_graph`
loads `pixel_lora` first, at `canonical.lora_strength`, and then chains
`style_lora` at `models.style_lora_strength`, default 0.6. pixel-art-xl
already runs at 1.2, so the two compete; the schema's own note is that if
the look goes muddy, bring ONE of the two down rather than pushing the other
up. Start at 0.4-0.6 and raise, since strength at inference is reversible
and an over-trained LoRA is not.

Test it on `canonical` alone before running a full sweep. A style LoRA that
ruins the anchor ruins everything downstream of it, and the anchor is one
image rather than a sheet.

## Epoch is not iteration

`batch_size` is 1, so one iteration is one image presentation, and one epoch
is `images x num_repeats`. They are equal only when
`images x repeats / batch_size == 1`, which is never the case here.

| tier | `num_repeats` | steps/epoch | epochs inside the 1800-step default |
|---|---|---|---|
| `hero` | 5 | 175 | ~10 |
| `good` | 3 | 105 | ~17 |
| `okay` | 1 | 35 | ~51 |

`num_repeats` is the per-folder weight, which is how a better image is seen
more often without duplicating the file.

## Running it

    ComfyUI/.venv/bin/python tools/train_prep.py add training_set/sprite/*.png --kind style --tier good
    ComfyUI/.venv/bin/python tools/train_prep.py status --kind style
    ComfyUI/.venv/bin/python tools/train_prep.py config --kind style

`add` copies into `training/style/3_good/` and appends provenance.
Every image needs an adjacent `.txt` caption; `status` counts the ones
without.

Defaults worth keeping: `--rank 8 --alpha 4 --lr 1e-4 --steps 1800`.
Rank is capacity rather than dataset size - 32 memorises twenty images.

## Do not pick the step count up front

`--save-every-n-epochs 1` is the default so every epoch survives. Compare
epochs on ONE fixed seed and prompt and keep the winner; the repo's own note
is that the difference is obvious between two epochs and invisible looking at
one. On 35 images the useful one is likely between 8 and 14, which is a guess
and the comparison is cheap.

## Captions

Name the pose, the view and the subject. Do NOT name the style: whatever is
written is attributed to those words, and whatever is left out is absorbed
into the trigger, which is the thing being trained.

## What matters in the data, and what does not

Varying is GOOD, per `pipeline/looks/training.py`:

- background colour - it cancels out and stops being learned
- palette - a dataset that is all blue teaches blue
- character, so the LoRA learns a look rather than a person
- pose, above all

So inconsistent backdrops and per-image keying residue are not a problem.
They vary, so they cancel. A set that shared ONE backdrop would be the
problem, because the LoRA would bake it in.

Holding constant matters:

- feature scale. The one thing that must not vary, and the reason this set
  only became trainable after pixelizing
- outline discipline - everything outlined or nothing
- figure scale in frame - full body throughout, which this set is

## Which group: small or large

`small` and `large` are not two sizes of the same thing. Measured on
`training_set/sprite`, cells across the canvas:

| group | n | cells | colours |
|---|---|---|---|
| `small` | 17 | median 64, range 64-256 | 12 |
| `large` | 18 | median 192, range 128-256 | 12 |

A 3x gap in the one property the gate says must not vary. Training them
together asks the LoRA to learn two feature scales and it will land between
them, which is the outcome this project has been rejecting by eye all week.

Prefer `small`, and not only for taste. SDXL's VAE downsamples by 8, so a
1024 canvas is a 128x128 latent and one pixel-art cell costs:

| cells across 1024 | latent cells per block |
|---|---|
| 64 | 2.00 - representable, four latent cells must agree |
| 128 | 1.00 - exact, one cell is one latent cell |
| 192 | 0.67 - below the grid; no edge can be placed there |
| 512 | 0.25 - below the grid |

`large` at 192 is asking for structure finer than the latent can hold. The
decoder answers with a gradient, not a block. Generation bears this out: of
49 scored runs, 45 sit at 512-1024 cells and read as smeared, and the single
run that measured 128 cells is the one that read as pixel art.

So 128 cells at 1024 is the design point, and `small` is the group nearer it.
Training on `large` is not a harder version of the same task; it is a
different task the architecture cannot do.

Not measured here: whether a LoRA trained on 64-cell art actually produces
64-cell output, or whether the model pulls back toward its own 128. Nothing
in this repo has trained one.

## What 12 colours costs

Mean absolute error per channel against the keyed original, over the subject
only, n=10:

| colours | mean err | p95 |
|---|---|---|
| 8 | 11.98 | 31.0 |
| 10 | 10.49 | 27.3 |
| 12 | 9.19 | 23.3 |
| 16 | 8.20 | 21.0 |
| 24 | 6.43 | 17.7 |
| 32 | 5.64 | 15.7 |

No knee - it is a smooth trade, so fidelity does not pick the number and
looking at the result does. 12 to 16 buys 11% less error for a visibly less
clustered image.

What the count must be is CONSTANT. Per-sprite palettes differing is wanted:
varying content cancels, so the LoRA learns "few, flat, well separated"
rather than a particular teal. It is the count that becomes the style.

The sources are full colour, median 42,667 distinct colours over the subject
after keying, so 12 is a real reduction rather than a match to the material.

## Before training, in order

    ollama serve                       # the captioner needs a VLM
    ComfyUI/.venv/bin/python tools/caption_training.py --trigger <word>
    ComfyUI/.venv/bin/python tools/flatten_training.py
    ComfyUI/.venv/bin/python tools/train_prep.py add --kind style \
        --tier good training_set/flat/*.png

`caption_training.py` writes one line per image naming the subject, the pose
and the view, and strips any style word the model reaches for. What a caption
names is attributed to those words; what it omits is absorbed into the
trigger, so a set of anime women captioned only `pixelart` teaches
"pixelart means an anime woman".

`flatten_training.py` puts #FF00FF behind each sprite. The sprites carry hard
alpha, kohya composites that onto something before training, nothing here says
what, and whatever it picks is the backdrop the LoRA learns. #FF00FF is what
the prompt already asks for and the keyer already removes, so the learned
backdrop is the one thrown away.

## Fix before training

`large_13` (a FLY AGARIC caption and a second object) and `large_15` (an
illufinch watermark) are moved to `training_set/rejected/`, with the reason
for each in `why.jsonl` beside them. A LoRA learns a watermark with total
reliability and then draws it on every output. 33 sprites remain.

Also known: `large_18`'s white dress lost pixels to keying, because the dress
and the backdrop are both (243,243,242) and no tolerance separates identical
colours. It is cosmetic for training and worth a hand fix if it bothers you.

## The risk this guide cannot measure

These are 35 different artists. The gate measures feature scale, which now
passes. It cannot measure whether the LOOK is consistent, and `hold:` asks
for outline discipline across the set.

If the result is mushy, suspect style variance rather than step count, and
cull to a coherent subset rather than training longer.
