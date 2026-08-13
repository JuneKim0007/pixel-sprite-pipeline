# Default open weights

Everything this runs on is open-weight and downloadable. Nothing here calls a
hosted API, and there is no account to create.

20.5 GB in total, which is why the repository does not carry them.

## What gets loaded

| role | file | size | licence |
|---|---|---|---|
| checkpoint | `sd_xl_base_1.0.safetensors` | 6.9 GB | CreativeML Open RAIL++-M |
| pixel LoRA | `pixel-art-xl.safetensors` | 171 MB | CreativeML Open RAIL-M |
| VAE | `sdxl_vae_fp16fix.safetensors` | 335 MB | MIT |
| ControlNet | `controlnet-union-sdxl-promax.safetensors` | 2.5 GB | Apache 2.0 |
| IP-Adapter | `ip-adapter_sdxl_vit-h.safetensors` | 698 MB | Apache 2.0 |
| CLIP vision | `CLIP-ViT-H-14.safetensors` | 2.5 GB | MIT |

Two more are installed and optional:

| role | file | size | when |
|---|---|---|---|
| alternate checkpoint | `illustrious-xl-v01.safetensors` | 6.9 GB | anime-leaning subjects; see the note below |
| LCM LoRA | `lcm-lora-sdxl.safetensors` | 394 MB | few-step sampling, not on by default |

The defaults live in `pipeline/shared/settings.py` and can be overridden per
pipeline in `library/configs/_global.yaml` or per style sheet.

## Where they come from

```
stabilityai/stable-diffusion-xl-base-1.0        sd_xl_base_1.0.safetensors
nerijs/pixel-art-xl                             pixel-art-xl.safetensors
madebyollin/sdxl-vae-fp16-fix                   sdxl_vae_fp16fix.safetensors
xinsir/controlnet-union-sdxl-1.0                promax variant
h94/IP-Adapter                                  sdxl_models/ip-adapter_sdxl_vit-h
laion/CLIP-ViT-H-14-laion2B-s32B-b79K           the vision tower IP-Adapter needs
OnomaAIResearch/Illustrious-xl-early-release-v0 optional checkpoint
latent-consistency/lcm-lora-sdxl                optional LoRA
```

They go in the usual ComfyUI folders: `ComfyUI/models/checkpoints`, `loras`,
`vae`, `controlnet`, `ipadapter`, `clip_vision`.

## Why these

**SDXL rather than SD 1.5 or Flux.** SD 1.5 has better small-model ecosystem
support but noticeably weaker prompt adherence, and adherence is what a
character description needs. Flux is stronger again and does not fit in 16 GB
alongside a ControlNet and an IP-Adapter.

**ControlNet Union rather than separate pose and depth models.** One 2.5 GB
file replaces two, which matters when the whole set has to be resident.

**The fp16-fixed VAE.** The stock SDXL VAE produces NaNs in fp16, which on MPS
shows up as black output rather than an error.

**IP-Adapter over textual inversion for identity.** No training step, and it
takes an image, which is the form a reference actually arrives in.

## Generating: what it needs

Measured on an M4, 16 GB, macOS 15:

| | |
|---|---|
| RAM | 16 GB works. Below that, expect swapping |
| disk | 21 GB for weights, plus outputs |
| GPU | Apple Silicon (MPS) or CUDA. CPU works and is impractical |
| one 1024 px image | ~280 s at 45 steps |
| a 4-view character sheet | 30-60 min including cooling |

Cooling defaults to 180 s between GPU tasks. Nothing needs it to work; it is
there so a night of generation does not hold the machine at its throttle point.

## Training a style LoRA: what it needs

Not yet part of the pipeline. These are the requirements, not a claim that it
has been run here.

Missing from the environment: `diffusers`, `peft`, `accelerate`. `torch` and
`transformers` are already installed.

| | |
|---|---|
| VRAM / unified memory | 16 GB is the floor for SDXL LoRA at 1024 px, with gradient checkpointing on and batch size 1 |
| disk | ~10 GB for checkpoints, one per epoch |
| images | 20+ for a style. `pipeline/looks/training.py` warns below that, because a smaller set reproduces its inputs rather than generalising |

A starting configuration:

```
resolution     1024
network dim    8-16          style needs less than character; 32 is for a face
network alpha  dim / 2
learning rate  1e-4
optimiser      AdamW8bit
batch size     1             not a choice at 16 GB
repeats        10
epochs         10            keep every checkpoint
gradient checkpointing  on   costs speed, buys memory
```

At 10 images and 10 repeats that is 100 steps per epoch, 1,000 total, which is
about right for a style LoRA. More and it memorises the set.

**Pick the epoch afterwards rather than the step count in advance.** There is
no reliable validation metric for a diffusion LoRA - the loss is noise
prediction error and tracks image quality only loosely - so `tools/train_prep.py`
emits `--save_every_n_epochs 1` and the winner is chosen by generating from
each checkpoint on a fixed seed and looking.

Wall-clock is unmeasured here. Generation runs at ~6 s per step at 1024 px, and
a training step carries a backward pass on top, so 1,000 steps is plausibly one
to two hours - but that extrapolation ignores optimiser state in memory, which
is exactly where a 16 GB machine starts swapping. Time 100 steps before
planning around it.
