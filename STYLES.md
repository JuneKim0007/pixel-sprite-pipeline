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

    styles/dark_fantasy.yaml            a look that is only words and numbers

    styles/retro_jrpg/
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
  Chunky readable sprites: large flat colour areas inside a firm dark
  outline, rather than fine per-pixel rendering.

vocabulary:                        # substituted into {placeholders}
  style:   [retro JRPG sprite, chunky pixels, thick dark outline]
  shading: [simple cel shading, two-tone shadows, no gradients]

modules:                           # per-pipeline-kind templates
  character_sheet:
    style: "{medium}, {style}, {shading}, plain flat background"

settings:                          # any config subtree
  palette:
    size: 28                       # colour count IS detail once shapes are flat
    factor: 8
    match: luma
  canonical:
    steps: 40
    lora_strength: 1.2
```

Inherited vocabulary groups merge, so a sheet states only what it changes. An
unresolved `{placeholder}` is left in the prompt rather than erased, so a typo
shows up in the preview instead of vanishing.

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
