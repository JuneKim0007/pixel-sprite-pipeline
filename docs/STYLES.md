# Style sheets

A named, reusable "look". This is the part of the repository worth carrying
between projects, so it is documented separately from the pipeline.

## Why a style sheet is not just prompts

An aesthetic decision used to have nowhere to live. "Pokémon-like, monochrome,
chunky" was retyped into `subject` and `style` for every pipeline and drifted
between them — while the settings that *also* carry a look (palette size, LoRA
strength, sampler, step count) sat somewhere else entirely.

So a sheet may set any config subtree. That breadth is the point: consistency
is the goal, and a tidy boundary that forces you to retype `lora_strength` in
three files does not serve it. It applies as a layer:

    global defaults  ->  style sheets  ->  pipeline config  ->  job overrides

A pipeline still wins over a style, and a single job still wins over everything.

## Four mechanisms of increasing strength

A look matures without changing the file's shape:

| mechanism | what it changes | signal needed | reversible |
|---|---|---|---|
| **vocabulary** | prompt fragments | instant | delete a line |
| **settings** | palette, sampler, steps | instant | delete a line |
| **exemplars** | IP-Adapter conditioning | one image | delete a file |
| **token / LoRA** | model weights | 20–40 images, hours of GPU | delete a file |

Palette settings are exact rather than suggestive, because snapping is
deterministic: frames cannot drift in colour.

## Two layouts

A sheet is either one file or a directory. Both are discovered.

    library/styles/base_pixel.yaml              a look that is only words and numbers

    library/styles/retro_jrpg/
      style.yaml                        the same document
      context/exemplars/*.png           dropped in — no YAML edit needed
      context/notes.md                  prose, read by the orchestrator
      training/images/*.png             outputs promoted as future LoRA data
      training/archive/<stamp>/         datasets already trained on
      history.jsonl                     append-only record of what changed

Relative paths resolve against the sheet's own folder, so a style folder is one
thing you can copy, share or delete.

The directory form exists because the three strengthening mechanisms have
different shapes — exemplars are files, training data is files, tuning history
is an append-only log — and none of them fit inside a YAML document.

**Exemplar auto-discovery is the point of the folder.** Adding a reference
should be a file copy, not a file copy plus a YAML edit, because the YAML edit
is the step people skip and then wonder why the look did not change.

## Writing one

```yaml
name: retro_jrpg
label: Retro JRPG (RPG Maker / Pokémon era)
extends: [base_pixel]              # depth-first, cycle-guarded
notes: >
  Chunky readable sprites: large flat colour areas inside a bold black
  outline, rather than fine per-pixel rendering.

vocabulary:                        # substituted into {placeholders}
  style:   [snes sprite, chunky pixels, bold black outline]
  shading: [flat cel shading, two tone shading]
  avoid:   [monochrome, desaturated]

modules:                           # per-pipeline-kind templates
  character_sheet:
    style: "{medium}, {render}, {style}, {shading}"

settings:                          # any config subtree
  palette:
    size: 28                       # colour count IS detail once shapes are flat
    factor: 8
    match: luma
  canonical:
    steps: 40
    lora_strength: 1.2
```

Inherited vocabulary groups **merge — they do not replace**. A child sheet adds
to a group; it cannot subtract from one. So a sheet states only its delta, and
restating an inherited term is how the same idea ends up in the prompt twice.
An unresolved `{placeholder}` is left in the prompt rather than erased, so a
typo shows up in the preview instead of vanishing.

## Choosing the words

The prompt fragments are not free text. Three rules decide them, and each one
came out of something that was measurably wrong.

### The positive prompt has no "not"

CLIP encodes a bag of concepts. There is no negation operator, so `no gradients`
tokenizes — in `openai/clip-vit-large-patch14`, the tokenizer SDXL's first text
encoder actually uses — as `no / gra / di / ents`. The content tokens are exactly
the thing being asked for, and prompt-level negation is documented to be
unreliable and to sometimes *produce* the suppression target
([TNG-CLIP, arXiv:2505.18434](https://arxiv.org/pdf/2505.18434); the Stability
SDXL demo's own guidance says exclusions belong in a
[separate negative input](https://huggingface.co/spaces/stabilityai/stable-diffusion/discussions/7857)).

`no anti-aliasing`, `no gradients` and `no dithering` were in the **positive**
prompt, and the negative prompt already said `antialiased, smooth gradient,
dithered`. They were paying tokens to argue against the negative.

Every such term now lives in the `avoid` group, expanded into
`canonical.negative` and `frames.negative` by `base_pixel`. `avoid` is the one
group where merging down the chain is exactly right: `retro_jrpg` adds
`monochrome, desaturated` because it is a colourful look, and `pokemon_mono`,
which extends `base_pixel` directly, must not inherit them.

The same fix applies to `BACKDROP_TERMS` in `pipeline/looks/vocabulary.py`,
which asked the positive prompt for `no shadow, no gradient, no ground plane`
while `BACKDROP_NEGATIVE` already listed `cast shadow, drop shadow, ground
plane, background gradient`.

### Prefer words with mass in the caption distribution

Evidence used, strongest first:

| grade | source | what it gives |
|---|---|---|
| model card | [nerijs/pixel-art-xl](https://huggingface.co/nerijs/pixel-art-xl) — the LoRA `models.pixel_lora` loads | "No trigger keyword require", instance prompt `pixel art`, example prompt `pixel art, a cute corgi, simple, flat colors`, negative `3d render, realistic` |
| shipped template | Stability's own SDXL style list, [`sdxl_styles_sai.json`](https://github.com/twri/sdxl_prompt_styler/blob/main/sdxl_styles_sai.json) | `sai-pixel art`: `pixel-art {prompt} . low-res, blocky, pixel art style, 8-bit graphics` / negative `sloppy, messy, blurry, noisy, highly detailed, ultra textured, photo, realistic`. `Retro game art`: `16-bit, vibrant colors, pixelated, nostalgic, charming, fun` |
| measurement | the CLIP BPE vocabulary itself | a phrase that survives as **one** token was frequent enough in the training captions to earn a merge. `snes` is one token; `JRPG` is `j`+`rpg`; `16-bit` is `1`+`6`+`-`+`bit` |
| forum | community SDXL pixel-art guides | `limited palette`, `clean edges`, `game sprite` — asserted, not documented; used only where a stronger source agreed |

**There is no missing trigger word.** The card states one is not required and
names `pixel art` as the instance prompt, which is already the first fragment
of `base_pixel.medium`.

### A term that instructs a human is not a term

| removed | from | why |
|---|---|---|
| `no anti-aliasing` | `base_pixel.render` | negation in the positive; the negative already says `antialiased` |
| `no gradients`, `no dithering` | `retro_jrpg.shading` | same; `smooth gradient` and `dithered` were already in the negative |
| `crisp hard edges` → `hard edges` | `base_pixel.render` | `crisp` is a texture/food adjective in alt-text; `hard edges` is the term of art and costs one token less |
| `flat colour blocks` → `flat colors` | `base_pixel.render` | the model card's own example says `flat colors`; `colour` and `color` are *different* CLIP tokens and the caption corpus is en-US. Also the literal duplicate: the same phrase sat in `retro_jrpg.style` |
| `retro JRPG sprite` → `snes sprite` | `retro_jrpg.style` | `JRPG` is not one token; `snes` is |
| `16-bit game art` | considered, dropped | `16-bit` is four tokens of digits and punctuation, and `snes sprite` already carries the era for two |
| `thick dark outline around the whole figure` → `bold black outline` | `retro_jrpg.style` | "around the whole figure" is a spatial instruction a bag-of-concepts encoder cannot act on; it only dilutes the rest |
| `two-tone shadows` → `two tone shading` | `retro_jrpg.shading` | `shadows` means *cast* shadows in caption text, which `BACKDROP_NEGATIVE` is actively suppressing — the style was fighting the backdrop |
| `full body filling the frame` → `full body` | `retro_jrpg.framing` | framing is enforced geometrically by `pose.fill`, at projection. The words asked the model to redo work already done |
| `head to feet visible` | `base_pixel` module templates | a second way of saying `full body`, next to `full body` |
| `centred` → dropped | `retro_jrpg.framing` | en-GB spelling, and the pose projection already centres the figure |
| `facing the viewer` | `retro_jrpg.framing` | **a real conflict.** `pipeline/stages/frames.py` appends `view_words(yaw)` to this same prompt, so a rear frame read `facing the viewer, ..., rear view, seen from behind` — and `facing_negative` then had to add `face, eyes, nose, mouth, front view` to fight the style's own words |
| `plain flat background` | both module templates | the backdrop clause already names a solid magenta chroma key; `vocabulary.BACKDROP_WORDS` classes `flat background` as a conflict, but the existing guard only inspects a config's own `style:` field, so a style *sheet* slipped past it |
| `clean saturated palette` → `vibrant colors` | `retro_jrpg.palette_words` | `vibrant colors` is Stability's own retro-game wording; `clean` is not a visual property |
| `strong value contrast` → `high contrast` | `retro_jrpg.palette_words` | `value` reads as numeric/monetary in caption text; `high contrast` is a standard photographic tag and is what `pokemon_mono` already used |
| `crisp clean pixel clusters` | `hi_fidelity.style` | "pixel clusters" is pixel-artist jargon, absent from alt-text |
| `readable silhouette` | `hi_fidelity.style` | an art-direction note, and actively dangerous: `silhouette` in captions means a black filled shape, so it pushes toward flattening the figure |
| `firm dark outline` | `hi_fidelity.style` | near-duplicate of the inherited outline term; `firm` is not a visual adjective |
| `simple` / `deliberate cel shading` → `flat cel shading` | both sheets | `deliberate` describes an intent, not an image. One phrase, shared with `pokemon_mono` |
| `three-tone shading with a rim light` → `rim light` | `hi_fidelity.shading` | groups merge, so `three tone shading` would have sat next to the inherited `two tone shading` and contradicted it. What separates this sheet is its palette size, steps and canvas — not a tone count |

Added: `limited palette` (`base_pixel.render`) and `detailed pixel art`
(`hi_fidelity.style`).

### The 77-token ceiling

CLIP takes 77 tokens per chunk. ComfyUI does not truncate past that — it closes
the chunk, opens a new one and concatenates the embeddings
(`comfy/sd1_clip.py`, the batching loop around `self.max_length`). Nothing is
lost, but everything after the boundary is encoded without the context of what
came before it, so the tail of a long prompt is weaker than its head. Counted
with the SDXL CLIP-L tokenizer, subject `a knight in armor`, backdrop clause
included, plus the per-frame view suffix:

| sheet | before | after |
|---|---|---|
| `retro_jrpg` | **102** — spilled | 66 |
| `hi_fidelity` | **133** — spilled | 73 |
| `pokemon_mono` | **78** — spilled by one | 63 |
| `dark_fantasy` | 68 | 53 |

Three of the four shipped sheets were over the line. `hi_fidelity` was worst,
and its cause was the merge: it restated four `style` and three `shading` terms
`retro_jrpg` had already contributed, because groups merge rather than replace.

The stacked negative (`avoid` + backdrop guard + pose guard) still runs to ~126
tokens and spills into a second chunk. That is left alone deliberately: a
negative is an unordered list of failure modes with no composition to break, so
a chunk boundary costs it much less than it costs the positive.

### Attention weighting

ComfyUI's `CLIPTextEncode` does honour `(term:1.3)` — `parse_parentheses` and
`token_weights` in `comfy/sd1_clip.py` parse it, and `encode_token_weights`
interpolates the token's embedding away from the empty-prompt embedding by that
factor. It is not used here. The weight is a blunt global scale on one concept,
and the thing it would be reached for — "more pixel, less painting" — is what
`lora_strength` already controls, on a dial this repository has actually
measured (1.2, the LoRA author's figure; higher speckles the blocks).

## History

Every foldered sheet keeps `history.jsonl` — append-only, one JSON object per
line, four event kinds:

    context   an exemplar or note was added or removed
    tune      a setting moved, with the evidence that moved it
    train     a LoRA was produced from a dataset
    note      a human wrote something down

A `train` event stores the dataset's **manifest, not the dataset**: names,
sizes, hashes, thumbnails. Once the weights exist the inputs are redundant, and
keeping them implies a reproducibility that is not there — the seed, the
optimiser state and the library versions are gone either way. So the images can
be archived or deleted without losing the account of what happened, and the UI
presents such entries as evidence, with no restore action.

A `tune` event without recorded seeds is not evidence, and says so. Variants
that differ in seed as well as in the setting measured seed luck.

## Training a sheet

The Styles tab's Training panel pairs guidance with a reading of the images
actually staged in `training/images/`. What it checks, and why, is in
`DECISIONS.md` — the short version:

- **Feature scale must be constant.** Detail one pixel wide and detail in
  eight-pixel blocks cannot both survive the same reduction.
- **Colour count does not matter.** Palette snapping is deterministic and runs
  afterwards.
- **Vary the pose, or caption it.** A LoRA learns whatever is constant.
- **No watermarks.** A LoRA learns them with total reliability.
- **No animation frames.** Near-duplicates make it memorise a character.

20 usable images minimum, 40 comfortable. Five good pictures belong in
`context/exemplars/`, not here — at that count a LoRA overfits and reproduces
its inputs.

## Shipped sheets

| sheet | for |
|---|---|
| `base_pixel` | the shared base every other sheet extends |
| `retro_jrpg` | chunky RPG Maker / early Pokémon idiom, 28 colours |
| `hi_fidelity` | extends retro_jrpg with more colours, steps and canvas; ~3× the GPU time. For a hero or a boss, wasteful for a field mob. |
| `pokemon_mono` | limited palette, single-creature framing |
| `dark_fantasy` | muted, high-contrast |
