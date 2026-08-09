# Configuring this for what you are making

Read this before changing numbers. It is also written so you can hand it to an
AI assistant and say *"read CONFIGURING.md and set this up for 32×32 sprites"* —
every section states what depends on what, which is the part that is easy to get
wrong by changing one value in isolation.

**The shipped default targets 128×128 character sprites at roughly seven head-
lengths, in a chunky flat-shaded anime idiom.** If that is what you want, change
nothing. Otherwise find your case below.

---

## The one relationship that governs everything

    sprite size = generation canvas ÷ palette factor

Both halves live in the style sheet, together, deliberately. Pinning one without
the other silently changes your output resolution, which is why they are not in
two different files.

| canvas | factor | sprite | who this is for |
|---|---|---|---|
| 512 | 8 | 64 | small overworld sprites, tiles |
| 1024 | 8 | **128** | **the default** |
| 1024 | 4 | 256 | detailed sprites, finer per-pixel work |
| 1280 | 10 | 128 | the default, from a more detailed source (~1.6× the time) |
| 2048 | 8 | 256 | chunky blocks at a large size — expensive |

**The factor is not a quality dial.** It is how many screen pixels become one
logical pixel. A larger factor gives blockier, flatter art; a smaller one gives
finer, fussier art. Choosing 256 by halving the factor gives you *finer* art at a
larger size, not the same art bigger. To get the same chunkiness at 256 you must
double the canvas, and SDXL is trained at 1024 — beyond about 1280 it starts
repeating anatomy.

### The ratio that decides whether it looks broken

The factor alone does not tell you the whole story. What matters is how much
detail the model drew before you reduced it:

    reduction = (canvas ÷ the model's native block size) ÷ sprite size

Measured on this machine, SDXL with the pixel LoRA at strength 1.2 draws in
**2-pixel blocks**, so a 1024 canvas holds 512 logical pixels of real detail.
Reducing that to 128 averages sixteen of them into each output pixel. At a 1280
canvas and factor 10 it is twenty-five.

That averaging is what "the dots look broken" means: structure destroyed before
quantisation, not noise added after it. A checkpoint that draws in 4-pixel
blocks halves the problem for free — see `DECISIONS.md`.

If your output looks mushy and no palette setting fixes it, this ratio is
usually why. Either accept a larger sprite, or use a model that draws coarser.

Everything below assumes you have picked a row from that table first.

---

## Common goals

### 32×32 sprites

For overworld or menu icons, where a character is a few dozen pixels tall.

```yaml
# in your style sheet's `settings:` block
canonical: {width: 512, height: 512}
frames:    {width: 512, height: 512}
palette:
  factor: 16          # 512 ÷ 16 = 32
  size: 12            # a 32px sprite cannot carry 40 colours
proportions:
  legs: 1.0           # tall proportions vanish at this size
  torso: 1.0
pose:
  fill: 0.92          # every pixel counts, so fill the frame harder
```

Two things change together and both matter. **Palette size must come down**: at
32px a sprite is about 700 opaque pixels, and 40 colours over 700 pixels is
noise, not shading. **Proportions must come back toward the shipped 5.2 heads**:
a seven-head figure at 32px gives the head four pixels, and a face cannot be
built from four pixels.

### 64×64 sprites, and tiles

```yaml
canonical: {width: 512, height: 512}
frames:    {width: 512, height: 512}
palette:  {factor: 8, size: 16}     # 512 ÷ 8 = 64
proportions: {legs: 1.15, torso: 1.05}   # about 5.5 heads
pose: {fill: 0.90}
```

64 is the usual size for Pokémon-era overworld work and for terrain tiles. See
the tileset section below — a tile has different rules from a character.

### 256×256 hero sprites

```yaml
canonical: {width: 1024, height: 1024, steps: 60, candidates: 4}
frames:    {width: 1024, height: 1024, steps: 45}
palette:  {factor: 4, size: 48}
```

Finer art, so the palette grows with it. Expect roughly 2× the wall-clock of the
default per image, plus whatever `candidates` multiplies it by.

### Faster drafts, quality later

```yaml
canonical: {steps: 20, candidates: 1}
frames:    {steps: 20}
```

Do not go below about 20 steps. Measured: under 20 the source is noisy, and the
median block reduction turns that noise into scattered pixels rather than
removing it. LCM is worse — at 8 steps the pose ControlNet is only partially
obeyed, because LCM settles composition in the first two steps and the control
cannot redirect it afterwards.

---

## Settings that cost time, ranked by what they buy

Give these to someone with a faster machine, or to your own overnight queue.

| setting | change | cost | what it buys |
|---|---|---|---|
| `canonical.candidates` | 1 → 4 | 4× **the canonical only** | The best value here. Every frame inherits the anchor, so improving it once improves everything. Batched by default; see the measurement below. |
| `canonical.steps` / `frames.steps` | 30 → 45 | +50% | Cleaner block edges. |
| canvas + factor | 1024/8 → 1280/10 | ×1.6 | The same 128px sprite reduced from a more detailed source. |
| `sampler` / `scheduler` | → `dpmpp_3m_sde` / `karras` | +10% | Fewer speckles. |
| `palette.match` | → `luma` | **free** | Preserves the value ramp when snapping to a fixed palette. |
| `palette.size` | 40 → 20 | **free** | Larger flat areas. Often what people mean by "chunkier". |
| `pose.fill` | → 0.88 | **free** | The figure fills the frame instead of 68% of it. |

**Candidates go out as one batch, and there is a measurement behind that.**
Four candidates at 1024 on a 16 GB Mac:

| | wall | swapins |
|---|---|---|
| sequential | 2096 s | 2,809,129 |
| **batch** | **1806 s** | **1,281,996** |

Batch is 14% faster and swaps less than half as much. This reverses what was
originally written here: a four-candidate run had died on a thirty-minute
timeout and that was assumed to be swap thrashing. It was not — batch takes
1806 s and the timeout was 1800 s. It missed by six seconds.

Sequential is still worth choosing for one reason: it is the only path that can
**rest between candidates**, so an overnight queue run for thermal reasons wants
it despite being slower. Set `canonical.batch_candidates: false` for that.

---

## Style sheets are where a look lives

Do not put these in a pipeline config. A style sheet is the thing you reuse
across characters, and it can set any config subtree — prompts, palette,
sampler, proportions, reference exemplars — because all of those carry a look.

    global defaults  →  style sheets  →  pipeline config  →  job overrides

A pipeline wins over a style, and a single job wins over everything. So a config
should hold **who the character is** — subject, references, seed — and nothing
about how it should look.

`STYLES.md` covers the format. The shipped sheets:

| sheet | targets |
|---|---|
| `base_pixel` | the shared floor: canvas, factor, backdrop, identity weights, proportions |
| `retro_jrpg` | chunky RPG Maker / early Pokémon, 28 colours |
| `hi_fidelity` | extends retro_jrpg with more colours, steps and canvas; ~3× the time |
| `pokemon_mono` | limited palette, single-creature framing |
| `dark_fantasy` | muted, high contrast |

To make your own, copy the closest one to `styles/<yourname>/style.yaml` and
change what disagrees. `extends:` merges the rest.

---

## References: four roles, and they are not interchangeable

The weights differ by an order of magnitude, which is why they cannot share one
list.

| role | what it says | weight |
|---|---|---|
| `identity` | who the character is — illustrations, concept art, photos | 0.80 |
| `style` | what the art should look like — good sprites in the target idiom | 0.35 |
| `pose` | a composition to reproduce | 1.00 |
| `palette` | colours to lock to | 1.00 |

A style reference at identity strength overwrites your character with the
exemplar. That is the failure the split exists to prevent.

**Label the view honestly.** A reference's weight falls off with angular
distance from the frame being generated: a front reference gets 0.85 on a front
frame and 0.45 on a rear one. Adding a correctly-labelled rear reference nearly
doubles the identity strength on rear frames. Mislabelling a front image as
`rear` is actively harmful — rear frames then take it at full strength and come
back with a front-facing character in a rear pose.

---

## Backgrounds

The default names a magenta backdrop in the prompt *and* keys that exact colour
out afterwards, so the two agree.

```yaml
background:
  enabled: true
  colour: "#FF00FF"
```

Asking for a "plain flat background" instead gets you a lit studio card with a
cast shadow, because that is what the words describe. Naming a colour also lets
the keyer remove that hue exactly rather than flooding in from the corners and
guessing — which matters for a gap enclosed by the character, where a corner
flood cannot reach.

Magenta rather than chroma green: green sits close to skin, cloth and foliage
tones, and every pixel it bleeds into along an edge is one the palette has to
spend an entry on. Change it if your character is magenta.

Set `enabled: false` when a scene is part of the art.

---

## Tilesets

*This section will be rewritten once the tileset generator exists. What is
written here is the intent, not a description of shipped behaviour.*

Tiles have different rules from characters and will get their own style sheets:

- **64×64 is the target.** Canvas 512 ÷ factor 8.
- **Seams matter more than anything else.** A character is judged on its
  silhouette; a tile is judged on whether it meets its neighbour without a
  visible line. That is a different objective and needs different conditioning.
- **No rig, no yaw.** A tile has no body plan and no viewing angle, so the pose,
  depth and rig machinery does not apply. The palette, style-sheet layering,
  queue and export machinery does.
- **The output contract is a tile set, not an image.** Auto-tiling engines want
  a specific count and arrangement of tiles with defined neighbour rules.

Until then, a top-down object can be generated as a character with `rig: none`,
which skips the skeleton entirely and relies on the prompt.

---

## Telling an AI to change this

These files are written to be read by an assistant. Useful things to say:

> Read CONFIGURING.md and set the style sheet up for 32×32 sprites.

> Read CONFIGURING.md and DECISIONS.md. My sprites come out too fussy — too
> much per-pixel detail. Which settings move it toward larger flat areas?

> Read CONFIGURING.md. I have a machine with 24 GB of VRAM. Which defaults were
> chosen for a 16 GB Mac and should be revisited?

`DECISIONS.md` holds the measurements behind every default here, including the
ones that are specific to one machine. An assistant that reads both will not
change a number that was set for a reason it cannot see.
