# Pixel sprite pipeline

Local pixel-art sprite generation. Describe a character, give it a reference
image, get a game-ready indexed sprite sheet — the same character, from four
sides, in a fixed palette.

Everything runs on your own machine. No API key, no per-image cost, no content
filter, no upload. Built and benchmarked on Apple Silicon (M-series, 16 GB).

Today it does **2D character illustration and animation**. UI elements and
tilemaps are what I am working toward next.

**There is no hosted version, and there is not going to be one.** Serving this
would mean renting GPUs to generate other people's sprites, which costs real
money per image — and the whole point is that the models are open weights that
run on hardware you already own. So it runs on your laptop instead of my bill.

---

## What it actually does

Diffusion models cannot draw pixel art. They draw a *picture of* pixel art:
1024×1024 continuous-tone output where the blocks happen to be about 8 px wide,
the edges are anti-aliased, and there are several thousand colours. It reads
correctly zoomed out and falls apart on the grid.

They also cannot draw *the same character twice*. A fixed seed with a changed
prompt gives you a different character in a similar pose — not your character
from a new angle.

This pipeline treats both as engineering problems rather than prompting
problems. The rule it is built around:

> **Use the model where novelty is wanted. Use deterministic code everywhere
> consistency is required.**

| You need | Handled by | Why |
|---|---|---|
| A character that didn't exist before | SDXL + a pixel-art LoRA | genuine novelty |
| That *same* character in a new pose | IP-Adapter, from your reference | conditions on an image, not a description |
| An exact pose and camera angle | an authored skeleton + ControlNet | the pose is an input, not a hope |
| Identical colours in every frame | palette extraction, then snapping | exact by construction |
| A real 128×128 indexed sprite | a deterministic pixelizer | grid phase, block reduction, bounded palette |

The last one is not optional. Skipping it gives you pixel-*ish* art. Running it
gives you something you can put in a game.

---

## Setup

You need roughly **25 GB of disk** and a machine with a GPU Metal or CUDA can
see. The repo itself is small; the engine and the weights are not, and they are
deliberately not committed.

### 1. Get the code

```bash
git clone https://github.com/JuneKim0007/pixel-sprite-pipeline.git
cd pixel-sprite-pipeline
```

### 2. Install the engine

The pipeline drives [ComfyUI](https://github.com/comfyanonymous/ComfyUI) over
its HTTP API. It lives inside the checkout and is gitignored.

```bash
git clone https://github.com/comfyanonymous/ComfyUI.git ComfyUI
git clone https://github.com/cubiq/ComfyUI_IPAdapter_plus.git \
          ComfyUI/custom_nodes/ComfyUI_IPAdapter_plus

uv venv --python 3.12 ComfyUI/.venv
uv pip install --python ComfyUI/.venv/bin/python torch torchvision torchaudio
uv pip install --python ComfyUI/.venv/bin/python -r ComfyUI/requirements.txt
uv pip install --python ComfyUI/.venv/bin/python ruff pyyaml pillow numpy scipy \
                                                 opencv-python-headless ruamel.yaml
```

**Python 3.12, not 3.13+.** PyTorch wheels lag the newest release, and the venv
is pinned for that reason. It is self-contained — it will not touch your system
Python.

### 3. Get the weights

Seven files, about 13 GB. Filenames matter: the pipeline resolves models by
name, so save them exactly as shown.

| file | into | from |
|---|---|---|
| `sd_xl_base_1.0.safetensors` | `ComfyUI/models/checkpoints/` | `stabilityai/stable-diffusion-xl-base-1.0` |
| `pixel-art-xl.safetensors` | `ComfyUI/models/loras/` | `nerijs/pixel-art-xl` |
| `lcm-lora-sdxl.safetensors` | `ComfyUI/models/loras/` | `latent-consistency/lcm-lora-sdxl` |
| `sdxl_vae_fp16fix.safetensors` | `ComfyUI/models/vae/` | `madebyollin/sdxl-vae-fp16-fix` |
| `controlnet-union-sdxl-promax.safetensors` | `ComfyUI/models/controlnet/` | `xinsir/controlnet-union-sdxl-1.0` |
| `ip-adapter_sdxl_vit-h.safetensors` | `ComfyUI/models/ipadapter/` | `h94/IP-Adapter` |
| `CLIP-ViT-H-14.safetensors` | `ComfyUI/models/clip_vision/` | `h94/IP-Adapter` (`models/image_encoder/model.safetensors`) |

A separate fp16-fix VAE is not optional: SDXL's baked-in VAE overflows in fp16
and decodes to black or NaN on some backends.

**These are the weights I happen to use, not the only ones that work.** They are
all open weights, and I picked this set because it captures the art style I am
personally going for. Everything here is swappable — if you want a different
look, download different open weights and point the config at them. See
[Using a different checkpoint](#using-a-different-checkpoint) below.

**Ollama is optional.** It is only used if you want an LLM to draft new poses.
Everything else works without it.

### 4. Check it

```bash
make up      # starts ComfyUI, the web UI, and Ollama if present
make check   # lints and validates every config — seconds, no GPU
```

`make check` failing straight after install means the clone or the weights are
incomplete. It is not a config problem.

---

## Running it

```bash
make run CONFIG=character_sheet     # or any config by name
```

Or open the web UI at `http://127.0.0.1:8000` — same pipeline behind a form,
with previews, a queue, and a rig editor.

A config says **who the character is**. It does not say how the art should look
— that lives in a style sheet, so one look can be reused across every
character.

```yaml
name: my_character
module: character_sheet
rig: humanoid
styles: [hi_fidelity]
subject: >
  a young woman archer with short pale cyan hair, a white cropped jacket,
  black thigh-high socks, chunky black boots

references:
  identity:
    - {path: inputs/archer.png, view: front}

canonical: {seed: 41207, candidates: 2}
```

Then `make check`, and run it.

### Getting a good result

**One reference per viewing angle is the single biggest lever.** A front
drawing alone leaves the model inventing the back of the costume differently in
every frame. Label them honestly — a front image labelled `rear` is worse than
no rear reference at all.

**Weapon-free references are worth arranging.** Identity conditioning carries
content, not just style: a bow in the reference becomes a bow in the output
whether or not anything asked for one. Props belong to animation configs, where
they get placed at the hand the geometry knows about.

**Anime or pixel-art references land better than painterly ones.** Identity runs
at roughly four times the weight of the style exemplar, so a photoreal reference
drags the rendering with it and the style sheet cannot outvote it.

**Look at the first result before queueing fifty.** Every frame inherits from
the anchor image, so a bad anchor is the cheapest thing to catch and the most
expensive to let run.

---

## What you can change

### The relationship that governs everything

    sprite size = generation canvas ÷ palette factor

| canvas | factor | sprite | for |
|---|---|---|---|
| 512 | 8 | 64 | small overworld sprites, tiles |
| 1024 | 8 | **128** | **the default** |
| 1024 | 4 | 256 | finer per-pixel work |
| 1280 | 10 | 128 | the default, from a more detailed source (~1.6× time) |

The factor is not a quality dial — it is how many screen pixels become one
logical pixel. A bigger factor is blockier, a smaller one is fussier.

### Settings ranked by what they buy

| setting | change | cost | buys |
|---|---|---|---|
| `canonical.candidates` | 1 → 4 | 4× the anchor only | the best value here — every frame inherits the anchor |
| `steps` | 30 → 45 | +50% | cleaner block edges |
| canvas + factor | 1024/8 → 1280/10 | ×1.6 | same sprite, more detailed source |
| `palette.match: luma` | — | **free** | preserves the value ramp when snapping |
| `palette.size` | 40 → 20 | **free** | larger flat areas — often what "chunkier" means |
| `pose.fill` | → 0.88 | **free** | figure fills the frame instead of 68% of it |

### Using a different checkpoint

The weights in the setup table are the ones I use, chosen because they capture
the art style I am going for. They are not the only open weights that work —
any SDXL-family checkpoint can be dropped into `ComfyUI/models/checkpoints/`
and named in `models.checkpoint`.

**If you want a chunkier, flatter look, Illustrious XL is the one I would try.**
Measured here on the same seed, same prompt, same LoRA, changing only the
checkpoint:

| | native block | native detail | sprite | reduction |
|---|---|---|---|---|
| Illustrious XL | **4 px** | 256 px | 128 px | **2.0 : 1** |
| SDXL base | 2 px | 512 px | 128 px | 4.0 : 1 |

That last column is what matters. It is how many logical pixels get averaged
into each output pixel — at 2:1 an output pixel decides between four inputs, at
4:1 it averages sixteen. Averaging sixteen hand-sized details into one pixel is
what "the dots look broken" actually is: structure destroyed before it can be
quantised, not noise added after. Illustrious draws at roughly the feature scale
this kind of work wants.

**The catch, so you do not waste a night on it:** at SDXL's CFG of 7.5 it
oversaturates and crushes blacks, giving a colour cast. Anime finetunes
generally want CFG 4–6, and it is trained on booru tags rather than natural
language. The shipped `_illu_cfg40` / `_illu_cfg50` / `_illu_cfg60` configs
sweep exactly that. SDXL base stays the default here only because that sweep
has not been run to conclusion.

The general lesson transfers to any checkpoint you try: **look at the block size
the model natively draws in**, not at how good its samples look at full
resolution. A model that draws coarser gives you a gentler reduction for free,
and no palette setting recovers detail that reduction already destroyed.

### Style sheets

**A look is a reusable file, not a prompt you paste around.** This is the part
worth understanding, because it is where most of the control lives.

An aesthetic decision used to have nowhere to sit. "Pokémon-like, monochrome,
chunky" got retyped into every config and drifted between them — while the
settings that *also* carry a look (palette size, LoRA strength, sampler, step
count) lived somewhere else entirely. A style sheet is one file that holds both,
applied as a layer:

    global defaults  →  style sheets  →  pipeline config  →  job overrides

A pipeline config still beats a style, and a single job still beats everything.

Five sheets ship with the repo:

| sheet | for |
|---|---|
| `base_pixel` | the shared base the others extend |
| `retro_jrpg` | chunky RPG Maker / early Pokémon idiom, 28 colours |
| `hi_fidelity` | more colours, steps and canvas; ~3× the time. A hero or a boss. |
| `pokemon_mono` | limited palette, single-creature framing |
| `dark_fantasy` | muted, high contrast |

A sheet can strengthen a look four ways, in increasing order of effort:

| mechanism | changes | needs | to undo |
|---|---|---|---|
| **vocabulary** | prompt fragments | nothing | delete a line |
| **settings** | palette, sampler, steps | nothing | delete a line |
| **exemplars** | image conditioning | one image | delete a file |
| **token / LoRA** | model weights | 20–40 images, hours of GPU | delete a file |

Exemplars are auto-discovered from the sheet's folder, so adding a reference is
a file copy — not a file copy *plus* a YAML edit, because the YAML edit is the
step people skip before wondering why the look did not change.

**A sheet may set any config subtree, and that breadth is deliberate** — but it
is also the honest catch. You *can* control this at a very fine grain, and once
you are several sheets deep it is genuinely hard to keep every weight, strength
and step count straight in your head. Which sheet set `lora_strength`? Did the
pipeline config override it, or the job?

**In practice, do not track it by hand.** Point an AI agent at this README and
let it set the values as you go — "make this chunkier at 64×64", "this is
washing out the red, what is pulling it" — and let it find which layer is
responsible. That is a much better use of the layering than memorising it.
`CONFIGURING.md` and `DECISIONS.md` carry the numbers and the measurements
behind them if you want to go deeper.

### Body plans

18 rigs, so this is not humanoid-only:

`humanoid` · `humanoid_tailed` · `humanoid_4arm` · `humanoid_6arm` ·
`quadruped` · `dragon` · `wyvern` · `avian` · `serpent` · `centipede` ·
`spider` · `scorpion` · `insect` · `beetle_winged` · `octopoid` ·
`tentacled_4` · `blob` · `none`

Humanoids are the only ones with a matching pose ControlNet, and they also
**rig themselves** from a reference — joints are fitted automatically and the
fit opens for review before it costs GPU time. Non-humanoids return a bounding
box instead, which is fine for a character sheet: the reference is supplying
identity, not composition.

---

## Features

- **Character sheets** — one character, four labelled views, consistent identity
- **Animations** — one action as N temporally related frames, with props placed
  at the hand the rig knows about
- **Swappable open weights** — nothing is hardcoded to one model; point the
  config at a different checkpoint, LoRA or VAE for a different style
- **Deterministic pixelization** — grid-phase detection, block reduction, and
  palette snapping, validated against synthetic ground truth
- **Fixed palettes** — bring a `.hex` file (PICO-8 and DawnBringer-32 included)
- **Automatic rigging** — humanoid joints fitted from a reference image
- **Authored pose library** — idle, breathe, attack, hit, fall, at any yaw
- **Optional LLM pose drafting** — via a local Ollama model
- **Web UI** — no build step; settings forms are generated from the schema
- **Job queue + autopilot** — submit many jobs and let it drain unattended;
  it distinguishes a broken job from one that is merely waiting on a dependency,
  and pauses rather than failing everything if the engine goes down
- **Thermal duty cycling** — configurable rest between GPU tasks, so an
  overnight run does not hold the machine at its throttle point
- **Style history** — an append-only log per style sheet

---

## Driving this with an AI agent

If you are pointing an agent at this project, **just tell it to read this
README first.** That is usually enough for it to work out what is going on and
start configuring things sensibly. The code base is not large, and an agent can
read the relevant file faster than either of us can look up which knob lives
where.

That is also why there is **no MCP server here.** I might add one if there turns
out to be enough interest, but I do not really see the value: MCP earns its
keep when a model needs a door into something it cannot otherwise reach, and
this is a small local repo of YAML and Python that any coding agent can already
read, edit and run. Pointing it at the docs gets you the same result without a
protocol in the middle.

Same reasoning for the web UI. It is deliberately plain — no build step, forms
generated from the schema — and it is a **local** frontend, for one person on
one machine, not something being dressed up for an audience. I may polish it,
but it is not where the value is.

## Roadmap

**Where it is now:** 2D character illustration and animation, which work end to
end.

**What I am building next:**

- **UI elements** — buttons, frames, panels, icons. Different problem from
  characters: no rig, and the constraint is crisp edges at small sizes rather
  than anatomy.
- **Tilemaps** — seamless, edge-matched tiles. There is experimental tileset
  support already; making it reliable is the work.

**Further out:**

- **A character LoRA trained from generated frames.** Identity conditioning is
  strong but not a guarantee; a LoRA trained on ~20 frames of a character is.
  The training-data preparation already exists.
- **Frame-to-frame coherence for animation.** Each frame is currently
  conditioned independently. Real in-betweening wants an external rigging step.
- **Finishing the checkpoint sweep.** A model drawing in coarser native blocks
  cuts the reduction ratio for free, which is the main source of mush at small
  sprite sizes. See [Using a different checkpoint](#using-a-different-checkpoint).
- **Non-Apple-Silicon paths.** Everything is CUDA-compatible in principle; only
  the launcher flags and the benchmarks are Metal-specific today.

**Maybe, if there is interest:**

- **An MCP server**, and **more polish on the local web UI**. Neither is a
  priority — see [Driving this with an AI agent](#driving-this-with-an-ai-agent)
  for why I think an agent plus this README already covers it.

**Not planned:** a hosted service. See above — the economics only work because
you run it yourself.

---

## Documentation

| file | for |
|---|---|
| `CONFIGURING.md` | changing settings for a different sprite size or look |
| `STYLES.md` | the style-sheet format |
| `OVERNIGHT.md` | setting up a batch and leaving it running |
| `PIPELINE.md` | how the stages fit together |
| `DECISIONS.md` | every default that was measured rather than guessed, with the measurement |

## Licence

The code here is yours to use. The models it downloads are not — SDXL,
`pixel-art-xl`, IP-Adapter and the ControlNet each carry their own licence, and
commercial use is your responsibility to check.
