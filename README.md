# Local pixel-art sprite generation — SDXL + pixel-art LoRA on Apple Silicon

Fully local. No API calls, no per-image cost, no content filter.
Built and benchmarked on **M4 / 16 GB / macOS 15.5**.

---

## Documentation

| file | for |
|---|---|
| `CONFIGURING.md` | changing settings for a different sprite size or look. Start here. |
| `STYLES.md` | the style-sheet format, and what a "look" is allowed to contain |
| `DECISIONS.md` | every default that was measured rather than guessed, and the measurement |
| `PIPELINE.md` | how the stages fit together |
| `AGENTS.md` | working in this repository, for an AI assistant or a new contributor |

## Why this shape

Diffusion models cannot actually draw pixel art. They draw a *picture of*
pixel art: 1024×1024 continuous-tone output where blocks happen to be ~8 px
wide, edges are anti-aliased, and there are thousands of colours. It looks
right zoomed out and falls apart on the grid.

So the pipeline is deliberately two stages, and the second one is not optional:

| Stage | Tool | What it fixes |
|---|---|---|
| 1. Generate | SDXL + `pixel-art-xl` LoRA | Composition, subject, style |
| 2. Pixelize | `tools/pixelize.py` | Grid alignment, block reduction, bounded palette |

Skipping stage 2 gives pixel-*ish* art. Running it gives an actual 128×128
indexed sprite you can put in a game.

---

## What got installed

```
pixel/
├── ComfyUI/                  # engine + its own Python 3.12 venv
│   ├── models/checkpoints/   sd_xl_base_1.0.safetensors        6.5G
│   ├── models/loras/         pixel-art-xl.safetensors          163M
│   │                         lcm-lora-sdxl.safetensors         376M
│   └── models/vae/           sdxl_vae_fp16fix.safetensors      319M
├── tools/gen.py              # CLI → ComfyUI HTTP API
├── tools/pixelize.py         # the stage-2 converter
├── palettes/                 # pico8.hex, dawnbringer32.hex
├── start.sh                  # launcher with the right Apple Silicon flags
└── out/                      # generated sprites
```

Two install details worth knowing, because they will bite on any rebuild:

- **Python 3.12, not your system 3.14.** PyTorch has no 3.14 wheels. The venv
  is pinned via `uv venv --python 3.12` and is self-contained.
- **A separate fp16-fix VAE.** SDXL's baked-in VAE numerically overflows in
  fp16 and yields black or NaN images on some backends. `sdxl_vae_fp16fix`
  avoids the whole class of problem.

---

## Running it

```bash
./start.sh                       # web UI at http://127.0.0.1:8188
```

Then either use the web UI, or drive it from the CLI:

```bash
# fast draft
tools/gen.py "a knight with a sword, idle stance, side view, pixel art sprite" --lcm

# 4 variations, batched (much cheaper than 4 separate runs — see benchmarks)
tools/gen.py "goblin archer, side view, pixel art sprite" -n 4 -b 4

# final quality
tools/gen.py "goblin archer, side view, pixel art sprite" --seed 1234

# then convert to real pixel art
tools/pixelize.py out/*.png -o out/px -f 8 -c 32 --alpha --upscale 4
```

**Dragging any generated PNG onto the ComfyUI canvas rebuilds its exact
workflow** — the graph is embedded in the file's metadata. That is the easiest
bridge between the CLI and the GUI.

---

## Measured performance (M4, 16 GB, 1024×1024)

```
fixed overhead per queued prompt   ~35 s     (text encode + VAE decode + setup)
marginal cost per sampling step    ~4.3 s
```

| Configuration | Time per image |
|---|---|
| LCM, 8 steps, one at a time | 70.8 s |
| LCM, 8 steps, **batch of 4** | **49.0 s** |
| Normal, 25 steps | ~2.5 min |
| LCM at 768² / 640² | 57.3 s / 52.8 s |

The dominant cost is a **fixed per-prompt overhead, not the sampling**. Three
consequences that are not obvious:

- **Batching is the single best lever.** `-b 4` cut per-image time 31%. Batch
  4 fits in 16 GB at 1024² with no offloading; the model never spilled.
- **Shrinking the canvas barely helps.** 640² only saves 25% over 1024², and
  SDXL degrades below its native 1024² training resolution. Not worth it.
- **Fewer steps hits a floor.** 1 step and 4 steps both take 39.3 s. Below
  ~8 steps you are paying overhead for worse images.

`--gpu-only` was worth ~8% for plain SDXL, and is **deliberately not set** —
see `scripts/ctl.sh`. Once ControlNet and IP-Adapter are loaded the working set
exceeds 16 GB, and the flag forbids offloading, so macOS swaps to disk instead:
measured ~5× slower end to end. Flux was never an option here: it wants ~64 GB
of unified memory to be usable.

---

## Prompting notes

`pixel-art-xl` needs **no trigger word**. LoRA strength **1.2** is the author's
recommendation and matches what worked here.

**LCM mode trades prompt adherence for speed.** At CFG 1.5 a test prompt that
said "holding a sword" produced a swordless knight. Use `--lcm` to explore
composition cheaply, then re-run the seed you like in normal mode (25 steps,
CFG 7) for the asset you actually ship.

Prompts that worked: subject, then pose, then view, then
`pixel art, game sprite, plain flat background`. A flat background is worth
asking for explicitly — it makes `--alpha` keying clean.

---

## How `pixelize.py` works

Three defects, three passes, in order:

1. **Grid phase detection.** The generated pixel lattice almost never starts at
   (0, 0). The script tries all `factor²` offsets and picks the one minimising
   intra-block variance. Sampling on the wrong phase straddles block edges and
   smears two logical pixels into one — this is the biggest single cause of
   muddy output, and nearest-neighbour resizing in any normal image editor gets
   it wrong.
2. **Block reduction** — `median` (default), `mode`, or `mean`.
3. **Palette** — median-cut to N colours, or snap to a fixed `.hex` palette.

Then optional edge-connected background keying to alpha, and nearest-neighbour
upscaling for viewing.

**Validated against synthetic ground truth** (known 16×16 sprite, upscaled 8×,
phase-shifted (3,5), Gaussian-blurred, noise added):

| Reduce mode | Structural accuracy | Mean channel error |
|---|---|---|
| **median** | **100.0%** | 8.35 |
| mean | 100.0% | 14.26 |
| mode | 70.3% | 37.01 |

Phase was recovered exactly. With a fixed palette, recovery was **100% exact**.
`mode` is the intuitive choice and the wrong one — per-pixel noise makes every
colour unique, so the "most common colour" is meaningless. `median` is default
for this reason.

The correct factor for this LoRA at 1024² is **8** (→ 128×128), confirmed by
sweeping factors and comparing normalised intra-block variance: 4 and 8 both
land on phase (0,0), and 8 is barely worse than 4 despite averaging 4× more
pixels per block — which only happens if 8 is the true grid.

```bash
tools/pixelize.py IMG -f 8 -c 32 --alpha --upscale 4    # typical
tools/pixelize.py IMG --palette palettes/pico8.hex      # fixed palette
tools/pixelize.py IMG -f 8 -c 0                         # keep all colours
```

---

## The real limitation: character consistency

This setup generates **excellent individual sprites**. It does not, on its own,
solve the thing that actually matters for an animated sprite sheet: *the same
character* across frames and facing directions.

Seeds do not carry identity. A fixed seed with a changed prompt gives you a
different character in a similar pose, not your character in a new pose. Asking
for a 5×2 sheet in one generation is unreliable — the model has no notion of
frame-to-frame coherence, which is why the recommended practice is to generate
frames individually and stitch them.

What actually fixes it, in increasing order of effort:

| Approach | What it pins down |
|---|---|
| **ControlNet (OpenPose / scribble)** | Pose and camera angle per frame — this is the direct fix for the "front vs. back view drifts" problem |
| **IP-Adapter** | Character identity, carried from one reference sprite to every other frame |
| **A LoRA trained on your character** | Strongest identity lock; needs ~20+ images of that character first |

The usual production recipe is IP-Adapter for *who* plus ControlNet for *what
pose*, with the base sprite from this pipeline as the reference. That is a
meaningful next build, not a flag to flip.

Ping me and I'll set it up.
