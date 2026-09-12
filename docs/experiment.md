# Open experiments: the style LoRA

Not the same file as `experiment.md` at the root, which is the live sweep log
for generation. This one is about the LoRA trained from
`training_set/sprite`: what is unanswered, what would answer it, and which
knob to reach for when a run disappoints.

Questions this set raises that are not yet answered, and the measurements
that would answer them. Anything settled leaves here and lands in
`training_guide.md` or the commit that settled it.

## 1. Outline discipline mostly holds - five sprites do not

**Measured 2026-09-12, after one wrong measurement.** The first pass counted
rim pixels darker than the sprite's 20th luminance percentile and concluded
6 of 35 were outlined. That tested whether the outline is BLACK, which is not
what discipline means: a darker shade of the local fill colour is an outline,
and most pixel art outlines that way rather than in black.

Re-measured as luminance step from the rim to the interior, hue agnostic:

| | count |
|---|---|
| rim darker than fill by >10 | **30 / 35** |
| rim on one dedicated palette entry (>35%) | 13 / 35 |
| rim NOT darker - flat or brighter | **5 / 35** |

The five: `large_02` (-79), `large_05` (-76), `small_02` (-9), `large_08`
(-5), `large_10` (+1). A rim brighter than the fill by 79 is either a
light-edged style or keying halo, and those two readings are worth telling
apart by eye before deciding.

So discipline largely holds. What varies is HOW the outline is drawn - 13
sprites commit a palette entry to it, the rest shade the edge - and whether
that distinction matters to a LoRA is unknown.

**What would settle it.** Look at the five outliers. If they are halo rather
than style, the keyer is the fix and the set is already disciplined. If they
are genuinely light-edged art, drop them: five images against a 35-image set
is a cheap trade for coherence.

## 2. Whether hand-retouching helps or hurts

**Not attempted, deliberately.** Smoothing the sprites by hand toward a
consistent look makes the retoucher the most consistent signal in the set,
and `training.py` is explicit that what varies cancels and what is held is
learned. Residual keying noise varies per image; a hand-applied style does
not.

The two entries this set hits on the `reject:` list are different, and are
worth fixing because a LoRA learns a watermark with total reliability:

- `large_13` carries a "FLY AGARIC" caption and a second object
- `large_15` carries an "illufinch" watermark

Both are croppable programmatically and should be, before any training run.

## 3. Style variance across 35 artists

**Unmeasured.** The gate measures feature scale, which now passes at
`{1px: 35}`. It cannot measure whether the LOOK is consistent, and these are
roughly 35 different artists.

Clustering on palette distance and edge character would say how many coherent
groups exist and how large the biggest is. If the biggest coherent group is
under 20, this set cannot train a clean style LoRA at any step count and the
answer is more art rather than more epochs.

## 4. What a style LoRA costs at the anchor

**Unmeasured, and cheap to measure.** `models.style_lora` loads in
`comfy.base_graph`, which `canonical.py:140`, `frames.py:165` and
`pixelise.py:87` all call. It stacks on pixel-art-xl at 1.2 rather than
replacing it.

So the first test of any trained LoRA should be `canonical` alone at
strengths 0.0 / 0.4 / 0.6 / 0.8. A style LoRA that moves the anchor moves
everything matched against it, and the anchor is one image rather than a
sheet.

## 5. The white-on-white keying limit

**Measured and not solvable by tuning.** `large_18`'s dress and its backdrop
are both (243,243,242). Tolerance 8 to 30 changes the result by 1.7%, because
the colours are identical and only connectivity could separate them - and the
dress touches the ground.

A shape-based segmenter - rembg/U2Net, BiRefNet - cuts by learned object
shape rather than colour and would not care. The cost is an ONNX dependency
and 100-200 MB of weights, and those models are photo-trained so pixel-art
edges come back soft and need re-thresholding. For one image in 35 that trade
is not worth taking; hand-fix it.

---

# Knobs, in the order to reach for them

A run that disappoints should be diagnosed from the top of this list, not the
bottom. Everything here is reversible at inference except the training
parameters, which need a re-run.

## First, before blaming the LoRA

| knob | where | default | try | what it tells you |
|---|---|---|---|---|
| `models.style_lora_strength` | config | 0.6 | 0.0, 0.4, 0.6, 0.8, 1.0 | 0.0 is the control. If 0.4 already looks like the training art, the LoRA is over-trained rather than weak |
| `canonical.lora_strength` | config | 1.2 | 0.8, 1.0, 1.2 | pixel-art-xl competes with the style LoRA. If the look is muddy, bring ONE down - the schema's own note |
| checkpoint epoch | `training/` | last | 6, 10, 14, 17 | `--save-every-n-epochs 1` keeps them all. Compare on ONE fixed seed |

Run these on `canonical` alone. It is one image, and a style LoRA that moves
the anchor has already moved everything matched against it.

## Then, the data

| knob | try | what it tells you |
|---|---|---|
| drop the 5 non-outlined sprites | vs all 35 | answers experiment 1, and costs only 5 images rather than 20 |
| drop `large_13`, `large_15` | always | watermarks are learned with total reliability |
| `--tier hero` on the best 10 | vs flat `good` | `num_repeats` weights without duplicating. If a subset is better, this finds it cheaply |

## Then, training parameters

| knob | default | try | note |
|---|---|---|---|
| `--rank` | 8 | 4, 8, 16 | capacity, not dataset size. 32 memorises twenty images |
| `--alpha` | rank/2 | rank/2, rank | higher alpha is a stronger effective learning rate |
| `--lr` | 1e-4 | 5e-5, 1e-4, 2e-4 | lower if early epochs already overfit |
| `--steps` | 1800 | 900, 1800 | a style LoRA saturates early; do not raise before lowering |

Change ONE at a time against a fixed seed and prompt. Two changes at once
answer nothing, and the difference between epochs is obvious in a pair and
invisible in a single image.

## Reading the failure

| symptom | most likely cause | knob |
|---|---|---|
| output looks like one training image | over-trained, or a near-duplicate in the set | earlier epoch; check for duplicates |
| a watermark appears | `large_13` or `large_15` still in the set | drop them |
| outlines appear sometimes | the 5 outliers in experiment 1, or rim halo | drop them and retrain |
| colours go muddy | two style signals competing | lower `canonical.lora_strength` OR `style_lora_strength`, not both |
| rear views change style | no rear views in the set | this set is single-view; that is a data gap, not a parameter |
| anchor is fine, frames drift | style LoRA is not the cause - it loads in both | look at `frames.ip_adapter`, not the LoRA |

## What is NOT worth adjusting first

- **step count.** It is the most reached-for and the least informative,
  because every epoch is already saved and comparable.
- **the palette.** Snapping is deterministic and pulls colour back; the gate
  says so explicitly. A muddy result is not a palette problem.
- **background cleanliness.** It varies across the set, so it cancels. Only
  a backdrop shared by every image would be learned.
