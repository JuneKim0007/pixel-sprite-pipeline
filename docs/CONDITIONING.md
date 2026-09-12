# Conditioning weights: what each one moves

Not `WEIGHTS.md`, which is the model files on disk. This is the dials a run
sets, what each measurably does, and what has been ruled out.

For what to set without the explanation, see `docs/WEIGHT_SETUP.md`.

Every number was measured on this project between 2026-09-11 and 2026-09-12,
on char1-char8 at 1024. Re-measure before trusting one elsewhere.

Two numbers describe an output and they pull against each other:

- **likeness** - CLIP cosine to the character sheet. Rewards copying an
  illustration, so it is a fidelity proxy and not a quality score.
- **cells** - how many pixel-art cells the model drew across the canvas.
  Fewer is chunkier. The training set's chunky half is 72-160; SDXL's latent
  is 128x128 at 1024, so 128 cells is one cell per latent and anything finer
  cannot have an edge placed in it.

Almost every dial below trades one for the other.

---

## The dials

### `canonical.from_reference.weight_type`

The largest single lever, and it is not a strength. It picks **which SDXL
attention blocks** the adapter may write to (`IPAdapterPlus.py:307-320`), and
`CrossAttentionPatch.py:102` returns 0 for any block not named - so an
unlisted block gets nothing, not less.

| value | blocks of 11 | carries |
|---|---|---|
| `linear` | all 11 | everything |
| `style transfer` | {6} | colour, material, rendering |
| `composition` | {3} | layout, structure |
| `style and composition` | {3, 6} | both, weighted separately |

Block identities are the InstantStyle result (arXiv 2404.02733) and they held
here: `style transfer` copied a sheet's hot pink background verbatim while
following the pose exactly; `composition` fought the pose guide and drew the
limbs twice.

### `canonical.from_reference.weight`

How much of the reference. Raising it raises likeness and costs cells,
because at `linear` the reference carries its own rendering along with the
character. 0.9 measured likeness 0.826 at 768 cells; the same reference
pixelised first gets 256 cells for 0.717.

### `canonical.from_reference.weight_composition`

The block-3 half when `weight_type` is `style and composition`. Exists so
shape and colour can be asked for separately. The node always had it; the
wrapper did not pass it until 2026-09-12, so it ran at the node default of 1.0
whatever the config said.

### `canonical.lora_strength`

Inversely related to block size **only when no pixelised reference is
present**: 0.8 drew block 3.0 and 1.2 drew 2.0. With one, that stops being
true - 0.8 and 1.0 both drew block 4.0, and 0.6 drew 2.5, which is worse
rather than chunkier. Once the reference carries the look the LoRA is no
longer the thing setting the grid.

Higher is more illustrative. Lower is not reliably chunkier.

### `canonical.style_emphasis`

Wraps the style terms in CLIPTextEncode's `(term:weight)` syntax. Verified to
reach the encoder. **Ruled out below** - it made pixel-ness worse.

### `canonical.controlnet.*` and `canonical.depth_controlnet.*`

Pose and body mass, and *when they stop*. `end_percent` is the one that bites:
the guide quits at 0.40 while an identity reference runs to 1.00, so from 40%
onward the reference is the only thing steering layout. That gap is what draws
a second pair of arms.

Frames inherit these from the anchor now. They used to declare their own and
held the guide 71% longer, which made frames trace the skeleton the anchor had
drawn a body over.

### `references.emphasis.source`

Masks the reference so only the subject is attended to. **Currently inert**:
`auto` needs a painted map and none of char1-char8 has one, so `attn_mask` is
None on every run and the reference goes in with its background. `subject`
would derive the mask from the silhouette. Not enabled; see Open.

### `canonical.style_weight`

Style exemplar adapters, applied at `style transfer`. Inert for `crisp`,
which carries no exemplars.

---

## Ruled out

- **Prompt emphasis.** `style_emphasis` 1.3 and 1.6 gave the worst pixel-ness
  measured: 1024 cells, block 1.0, against `linear`'s 768 at the same
  likeness. The weighted term reaches the encoder and the IPAdapter writing
  all eleven blocks overrules it. Words lose to images.
- **Low LoRA under a pixelised reference.** 0.6 drew block 2.5 where 0.8 and
  1.0 drew 4.0. Guarded in `tools/sweep.py`.
- **Hand-mixing shape and colour at an illustration reference.**
  `weight` 0.2 with `weight_composition` 0.9 took neither: likeness 0.684,
  cells 512. Block 3 is the block the pose guide also drives, so "take the
  shape" imports a pose that already exists.
- **The LoRA ladder above 0.8.** Head block size did not move between 0.8 and
  1.1 on 8 of 8 characters, and higher is the illustrative direction.
- **`pose.fill` as a framing lever.** Returns about half what it is asked:
  0.88 drew 0.96, 0.80 drew 0.92.

## What each mode is good at

| mode | good at | costs |
|---|---|---|
| `linear` at an illustration | fidelity - likeness 0.826, the best measured, and facial detail survives | 768 cells, the reference's background and rendering come too |
| `style transfer` | pose obedience, and the lowest bleed when the backdrop is wanted | copies the backdrop verbatim; 512 cells |
| `linear` at a **pixelised** reference | the only mode reaching 256 cells / block 4.0, with the pose followed and bleed 0.017 | ~0.11 likeness against `linear`, and small detail is gone before the model sees it |
| `composition` | nothing measured yet that the others do not do better | worst likeness at 0.650, limbs drawn twice |

## Open

- Whether masking the reference (`emphasis.source: subject`) removes the
  background transfer without costing likeness. The knob exists and is inert.
- Whether `weight` above 1.0 recovers the fidelity a pixelised reference
  costs. 1.05 is running.
- Whether any of this survives a trained style LoRA, which would make the
  reference's job identity alone.
