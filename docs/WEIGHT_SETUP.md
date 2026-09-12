# Weight setup: what to set, and what it does

A lookup table. Pick the row that matches what you want, copy the settings.

Why any of it behaves this way is in `docs/CONDITIONING.md`. Nothing here
explains; it only reports.

Measured 2026-09-12 on char1, char2, char6, char8 at 1024, all on one pose
guide. Re-measure before trusting a number against a different set.

---

## Reading the columns

| column | means | better is |
|---|---|---|
| likeness | CLIP cosine to the character sheet | higher |
| cells | pixel-art cells across the canvas | lower is chunkier; target 72-160 |
| block | pixels per cell | higher is chunkier |
| bleed | share of the subject wearing the backdrop's colour | lower |

---

## Modes, chunkiest first

| mode | likeness | cells | block | bleed | n |
|---|---|---|---|---|---|
| pixelised ref, lora 1.0 | 0.720 | **256** | **4.0** | 0.030 | 3 |
| pixelised ref, lora 0.8 | 0.717 | **256** | **4.0** | **0.017** | 5 |
| pixelised ref, lora 0.6 | 0.678 | 640 | 2.5 | 0.030 | 2 |
| style transfer | 0.743 | 512 | 2.0 | **0.007** | 3 |
| shape + colour split | 0.684 | 512 | 2.0 | 0.032 | 6 |
| composition | 0.650 | 512 | 2.0 | 0.025 | 6 |
| prompt emphasis 1.6 | 0.799 | 768 | 1.5 | 0.034 | 2 |
| prompt emphasis 1.3 | **0.821** | 1024 | 1.0 | 0.007 | 2 |
| linear, weight 0.4 | 0.722 | 1024 | 1.0 | 0.028 | 1 |
| linear, weight 0.9 | **0.826** | 768 | 1.5 | 0.036 | 8 † |

† on the older pose guide; not comparable to the rows above it.

---

## Pick by what you want

### Chunkiest pixels

```yaml
references:
  identity: [{path: characters/<char>/front_px.png, view: front}]
canonical:
  lora_strength: 0.8
  from_reference: {weight: 1.05, weight_type: linear}
```

256 cells, block 4.0, bleed 0.017. Costs about 0.11 likeness against the
fidelity row. Small detail is gone before the model sees it.

### Most faithful to the sheet

```yaml
references:
  identity: [{path: characters/<char>/front.png, view: front}]
canonical:
  lora_strength: 0.65
  from_reference: {weight: 0.9, weight_type: linear}
```

0.826 likeness. Facial detail survives. 768 cells, and the reference's
background arrives with it.

### Pose obedience above everything

```yaml
canonical:
  from_reference: {weight: 0.9, weight_type: style transfer}
```

Two arms, T-pose, hands in the right place, every run. Copies the sheet's
backdrop verbatim - use a sheet whose backdrop you want.

### Lowest bleed

Same as pose obedience: 0.007.

---

## Do not set these

| setting | what happens |
|---|---|
| pixelised ref with `lora_strength` below 0.8 | 640 cells instead of 256. Refused at plan time. |
| `weight_type: composition` | 0.650 likeness, limbs drawn twice. Needs `_allow_comp`. |
| `style_emphasis` above 1.0 | 1024 cells, block 1.0. The flattest output measured. |
| `weight` 0.2 with `weight_composition` 0.9 | 0.684 likeness at 512 cells. |
| `lora_strength` above 0.8 with no pixelised ref | head block does not move; finer everywhere else. |

---

## Ranges

| setting | min | max | in use |
|---|---|---|---|
| `canonical.lora_strength` | 0.0 | 2.0 | 0.65 - 1.3 |
| `canonical.from_reference.weight` | 0.0 | 1.5 | 0.35 - 1.05 |
| `canonical.from_reference.weight_composition` | 0.0 | 2.0 | 1.05 |
| `canonical.style_emphasis` | 0.5 | 1.8 | 1.0 |
| `canonical.controlnet.strength` | 0.0 | 1.5 | 0.55 |
| `canonical.controlnet.end_percent` | 0.0 | 1.0 | 0.40 |
| `canonical.depth_controlnet.strength` | 0.0 | 1.5 | 0.30 |
| `canonical.depth_controlnet.end_percent` | 0.0 | 1.0 | 0.35 |

`frames.controlnet.*` and `frames.depth_controlnet.*` follow the anchor's
values unless set.

---

## Not yet measured

- `from_reference.weight` above 1.05.
- Any of this against a trained style LoRA.
