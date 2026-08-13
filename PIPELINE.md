# The sprite pipeline

Multi-stage, config-driven sprite generation. `README.md` covers the base
SDXL setup; this covers the pipeline built on top of it.

```bash
make up                          # ComfyUI + Ollama + web UI, waits until healthy
                                 # -> http://127.0.0.1:8000
make run CONFIG=knight_attack    # run a pipeline
make down                        # stop everything
```

`make status` shows health, PIDs and the latest run; `make logs` follows all
three services; `make check` validates every config without running anything.

`make up` is idempotent — a service whose port already answers is adopted, not
restarted — and `make down` only stops what make started, so an Ollama running
under `brew services` is left alone.

Service logic lives in `scripts/ctl.sh` rather than the Makefile because macOS
ships GNU Make 3.81, which predates `.ONESHELL`: every recipe line is its own
shell, so any loop would have to be crammed onto one backslash-continued line.

Underneath, if you prefer running the pieces yourself:

```bash
./start.sh                                          # ComfyUI (GPU stages)
ollama serve                                        # only for LLM poses
python server.py                                    # web UI
python run.py library/configs/knight_attack.yaml            # headless run
python run.py library/configs/knight_attack.yaml --explain  # validate + show the plan
python run.py --list-stages                         # what exists
```

## Two settings that cost 5x if wrong

Found by measurement, not by reading docs. Both are already set correctly.

**Do not use `--gpu-only` with this pipeline.** It is worth ~8% for plain SDXL,
but the pipeline also loads ControlNet (2.4 GB), IP-Adapter and a CLIP vision
encoder (2.4 GB). That working set exceeds 16 GB, and the flag forbids ComfyUI
from offloading, so macOS swaps to disk instead. Measured: **113 s per frame
without it, on track for ~9 minutes with it**, at 11.6 GB of swap.

**Pose control does not work under LCM.** At 8 steps the skeleton is only
partly obeyed even at `end_percent: 0.85`, because LCM settles composition in
its first couple of steps and ControlNet cannot redirect it afterwards. At 20
steps the pose lands. Frames therefore default to 20 steps, ~210 s each.

A third, smaller one: the Union ControlNet defaults to guessing its input type
(`auto`). Setting `union_type: openpose` explicitly is the difference between
the pose being applied and silently ignored.

---

## The one idea worth understanding

**AI is used where novelty is wanted; deterministic code is used where
consistency is required.** Those are different jobs and diffusion is only good
at the first.

| Needs | Handled by | Why |
|---|---|---|
| A character that didn't exist before | SDXL + LoRA | Genuine novelty |
| The *same* character in a new pose | IP-Adapter | Conditions on a reference |
| An exact pose | Authored skeleton + ControlNet | Pose is input, not a hope |
| Identical colours across frames | Palette extract + snap | Exact by construction |
| Frame-to-frame coherence | Rigging tool (external) | A guarantee, not a sample |

Every consistency problem that survived earlier attempts came from asking a
stochastic process for a deterministic guarantee. The stages below move each
of those to code.

---

## Stages

```
pose → depth → canonical → frames → palette → export
```

| Stage | Resource | Consumes | Produces |
|---|---|---|---|
| `pose` | CPU | — | `skeletons`, `pose_frames` |
| `depth` | CPU | `pose_frames` | `depthmaps` |
| `canonical` | GPU | — | `canonical` |
| `frames` | GPU | `skeletons`, `canonical`, *(`depthmaps`)* | `frames` |
| `palette` | CPU | `frames`, `canonical` | `palette`, `pixel_frames` |
| `export` | CPU | `pixel_frames` | `sheet` |

`depth` is optional — remove it from `pipeline.stages` and `frames` simply
won't use it.

Order lives in `pipeline.stages` in the config and is **validated before
anything runs**:

```
$ python run.py library/configs/bad.yaml --explain
PipelineError: stage 'frames' cannot run in this order:
  'canonical' is produced by 'canonical', which runs later
  'skeletons' is produced by 'pose', which runs later
```

Adding a stage is a class with `requires` / `produces` / `resource` plus an
import in `pipeline/stages/__init__.py`. Nothing else needs to know it exists.

### On parallelism

Consecutive CPU stages that don't depend on each other run concurrently, and
`palette` parallelises internally across frames in a process pool.

**GPU stages never run concurrently, and that is deliberate.** There is one
Metal device and SDXL plus ControlNet plus IP-Adapter already fill most of
16 GB. Overlapping them trades a small overlap for a large amount of swapping.
The LLM has the same problem from the other side, which is why `keep_alive: 0`
unloads it the moment pose generation finishes.

### Why not Docker

Docker Desktop on macOS runs a Linux VM with no Metal passthrough. Containerised
GPU stages would lose MPS and fall back to CPU. Isolation here is process-level
instead: stages are modules behind a declared interface, GPU work shares one
ComfyUI process, the LLM is a separate Ollama service.

---

## Poses: body space and viewing angles

Poses are stored as `(lateral, depth, height)` per joint — relative to the
character, not the camera — and projected to a viewing angle at render time.

```
lateral  -/+  the character's right / left
depth    -/+  behind / in front of the character
height   0..1 top of head to feet
```

So one `attack.json` serves every camera angle:

```yaml
pose:
  view: side           # front(0) three_quarter_front(40) side(90)
                       # three_quarter_rear(145) rear_turned(170) rear(180)
                       # or any number of degrees: `view: 160`
```

**This is the fix for front and back views collapsing into each other.** Past
roughly 100°, the nose and eye keypoints are dropped automatically — the
presence or absence of a face is how a skeleton tells ControlNet which way the
character is pointing. A rear view that still carries a nose keypoint reads as
a front view.

Preview the whole library at every angle:

```bash
tools/make_poses.py --preview --views all   # library/poses/preview/*.png
```

### Why poses are authored, never estimated

Pose estimators (OpenPose, DWPose) are trained on photographs and degrade badly
on game sprites — exaggerated proportions, hidden limbs, non-human shapes. The
*Sprite Sheet Diffusion* authors hit exactly this and fell back to annotating by
hand. So the skeleton is an **input** to generation, never recovered from
output. That also makes it exact, diffable, and reusable across characters.

### LLM-generated poses

```yaml
pose:
  source: llm
  action: a character swinging a sword downward, ending in follow-through
  frames: 6
```

This works because of a division of labour. An LLM is good at the qualitative
part ("the sword arm points forward and down") and bad at the quantitative part
("the wrist is 0.148 units from the elbow"). So:

1. The model proposes joint positions.
2. `snap_to_anatomy` keeps each bone's **direction** and rescales it to its
   exact neutral **length**, walking outward from the neck.
3. `validate_pose` checks what remains — ranges and structure.
4. Failures are fed back as explicit criticism ("bone l_shoulder→l_elbow is
   1.39x its neutral length") and retried.

Anatomy becomes correct by construction rather than by luck, so the generator
only has to be right about something it is actually good at.

**Honest quality note:** qwen3:4b produces valid but timid motion. Passing a
hand-authored sequence as a few-shot example (done automatically) improves it
markedly — stance and torso lean appear — but hand-authored poses in
`library/poses/*.json` are still clearly better. Treat `source: llm` as a way to draft
a new action quickly; accepted poses are cached to `library/poses/generated/` as
ordinary library files, so you can edit one into shape and keep it.

### Depth maps: the channel a skeleton can't carry

Three-quarter-rear (145°) and full-rear (180°) project to almost identical
keypoints — horizontal spread changes by only ~18% — yet look clearly
different. A 2D skeleton structurally cannot express viewing angle; people read
it from body volume and occlusion.

The `depth` stage supplies that. It is **computed, not estimated and not
authored**: every body-space joint already carries a depth coordinate, so the
map is `z = depth·cos(yaw) + lateral·sin(yaw)` rendered as depth-shaded
capsules, painted far-to-near so nearer limbs occlude farther ones. No model
loads; it takes milliseconds on CPU.

It stacks with the pose ControlNet because the Union model handles both types —
no second set of weights. Keep `depth_controlnet.strength` (0.45) **below** the
pose strength: depth describes volume, and pushing it hard makes sprites look
rendered rather than drawn.

This is the same trade as the palette: exact and free, because it is
computation rather than inference. The honest limit is that it's a crude
capsule model — good enough to condition on, not a real render.

### Animation vs. pose sets

Both use one code path, because they are the same operation — a shared
reference plus one skeleton per output. There is no mode flag; there is a
different input.

```yaml
# animation: one action, N temporally related frames
pose:
  source: library
  name: attack

# pose set: N independent poses, each optionally at its own angle
pose:
  set:
    - {name: idle,   view: front}
    - {name: idle,   view: three_quarter_rear}
    - {name: attack, frame: 4, view: side}
    - {source: llm, action: crouching low behind a raised shield}
```

A turnaround is just the same pose at several yaws.

**Do not chain frames off each other.** It is tempting to feed frame *i* into
frame *i+1* via img2img for continuity, but introducing a pose signal over an
existing pose [causes ghosting and blurring in exactly the areas that
move](https://arxiv.org/html/2404.13680v3). Consistency comes from the shared
reference, not from ordering — which is why `denoise` defaults to 1.0 and every
frame is generated independently.

### View-labelled references

IP-Adapter transfers identity from a reference image. Give it a front view and
ask for a rear sprite, and it does the only thing it can — pulls the result
back toward the front, fighting the pose control.

```yaml
references:
  # Typed by the job each image does. The flat `images:` list this used to
  # show now raises an error: the weights differ by an order of magnitude
  # between roles, so a style exemplar in an identity slot overwrites the
  # character with the exemplar.
  identity:
    - {path: refs/knight_front.png, view: front}
    - {path: refs/knight_rear.png,  view: rear}
  style: []      # good sprites in the target idiom, at ~0.35 weight
  pose: []       # a composition to reproduce
  palette: []    # colours to lock to
  match:
    tolerance_degrees: 40
    exact_weight: 0.85     # a reference that matches this view
    far_weight: 0.45       # a reference 180 degrees away
    auto: true
```

Each frame is matched to the nearest reference by angular distance, and **the
weight falls off with that distance**. This is the counter-intuitive part: when
you have no matching reference, you want *less* identity lock, not more. High
weight on a mismatched reference produces a front-facing sprite in a rear pose;
a weak hint leaves the model free to invent the side it has never seen.
Constraint where there is evidence, latitude where there is not.

Measured behaviour with front + rear references:

| Frame view | Chosen | Distance | Weight |
|---|---|---|---|
| 0° | front | 0° | 0.85 |
| 90° | front | 90° | 0.71 |
| 120° | rear | 60° | 0.79 |
| 180° | rear | 0° | 0.85 |

With only a front reference, a 180° frame automatically drops to 0.45.

### Palette groups

Palettes live in `library/palettes/<group>/<name>.hex` and may carry metadata:

```
// name: Dungeon Steel
// tags: cold, metallic, armor, knight, grim
// Restrained cool greys with one warm accent.
0D0F14
...
```

`palette.source: llm` picks one by subject. Verified: "knight in steel armor"
→ `character/dungeon_steel`, "forest ranger with a bow" → `character/forest_scout`,
"damp underground cave tileset" → `environment/cavern_damp`.

**The LLM only chooses; application stays deterministic.** That split is the
point — a model is a reasonable judge of which palette suits a subject and a
terrible mechanism for applying one identically across six frames. A colour
agent is a convenience, not a capability; `extract` remains the default because
a palette derived from the canonical always matches the art.

---

## Output layout

```
out/runs/20260809_143022_knight_attack/
├── config.yaml          snapshot of the exact config used
├── run.log              live stage progress
├── artifacts.json       what each stage produced
├── 00_pose/             skeleton_*.png + pose.json (poses, yaw, mode)
├── 01_depth/            depth_*.png
├── 02_canonical/        canonical.png
├── 03_frames/           frame_*.png
├── 04_palette/          palette.hex + frame_*_px.png
└── 05_export/           sheet.png + sheet.json (cell geometry)
```

Folders are numbered by execution index, so the listing reproduces the order
that run actually used — which matters when order is configurable.

---

## Training with per-image weights

```bash
tools/train_prep.py add out/runs/*/0?_palette/*_px.png --tier hero --caption "knight"
tools/train_prep.py add newer/*.png --tier good --boost 2
tools/train_prep.py status
tools/train_prep.py config > training/sprite/dataset_config.toml
```

The three controls you wanted map onto one mechanism and one convention:

| Want | How |
|---|---|
| Train only on images I picked | Files are added explicitly; nothing is swept in |
| Different weight per image | kohya reads `5_hero/` as "repeat these 5x per epoch". **Repeats are the weight** — no custom training code |
| Weight recent data higher | `--boost` adds repeats on top of the tier |

For a stronger recency effect, train a separate LoRA on new data and merge with
explicit ratios instead of inflating repeats:

```bash
python sdxl_merge_lora.py --models old.safetensors new.safetensors \
                          --ratios 0.7 0.4 --save_to merged.safetensors
```

That keeps each run an independent artifact you can re-weight or roll back,
rather than one adapter that silently drifts. `training/<kind>/provenance.jsonl`
records where every image came from and at what weight.

On hardware: SDXL LoRA training needs ~12 GB and is slow on MPS. Draw Things
does on-device SDXL fine-tuning at ~10.3 GB peak and is the practical Mac-native
option; renting a cloud GPU for a few dollars is usually a better use of time.
A character LoRA wants ~20+ images of the same subject, which is the bootstrap
problem — generate a batch with this pipeline, hand-fix the best in Aseprite,
then train.

---

## Web UI

```bash
python server.py        # http://127.0.0.1:8000
```

A static page cannot read your output directories, edit configs, or start a
run, so there is a small stdlib-only backend that reuses the pipeline package
directly. Loopback only, no auth — don't expose it.

| Tab | What it does |
|---|---|
| **Input** | Per-job values: prompts, reference upload/browse with view labels, folders |
| **Run** | The guided flow — Review → Rig editor → Check → Confirm, with Back at every step |
| **Result** | Per-stage sections, each viewable as a grid, an animation, or a joined sheet |
| **Settings** | Every knob, category sidebar, global defaults with per-pipeline overrides |

The split is by *rate of change*, not by feature: Settings holds how the
machine behaves (87 fields, set once), Input holds what you're making this
time, Run is the flow, Result is what came out.

Forms are generated from `pipeline/schema.py`, so a setting is defined in one
place: add a field there and it appears in the UI with the right control and
range. The UI hardcodes no knobs. Lists of objects — reference images, pose
sets, soft-body nodes — get bespoke editors, since a flat field table cannot
express them.

### Global defaults vs per-pipeline

`library/configs/_global.yaml` holds the machine-level answers (compute, models,
paths); a pipeline config carries only what it deliberately differs on. In
Settings, the scope switcher toggles between them, a pinned field shows a dot,
and **reset** removes the override so the value inherits again.

Presence is the override, not difference: a pipeline pinning `cfg: 7.0` when
the global is also 7.0 stays pinned to 7.0 — otherwise changing the global
would silently move it.

### The rig editor

Two orthogonal canvases, because **a 2D drag can only set two of the three
body-space coordinates**. Front sets lateral + height, side sets depth +
height. Dragging in one view updates both live.

Bone lengths are re-snapped on every drag, so moving a wrist rotates the
forearm instead of stretching it — the dragged joint keeps exactly where you
put it, and everything downstream of it follows.

Overlays share the same canvas: skeleton, computed depth map, and a reference
image at adjustable opacity. Reference mapping is an overlay mode rather than
a separate page, so calibrating a rig to your art uses the same interaction as
posing it.

### Gates and resume

`pipeline.stop_after: pose` stops the run after the pose stage. Edit the
skeletons in the rig editor, press Save — which re-renders the skeleton PNGs
*and* the depth maps, since the frames stage consumes images, not JSON — then
Resume. The runner skips completed stages, restores typed artifacts, and
continues folder numbering rather than restarting at `00_`.

The confirm dialog before a gated run carries a "don't show this again" box,
remembered in `ui.suppress_gate_confirm`.

**Docker is not used, deliberately.** Docker Desktop on macOS runs a Linux VM
with no Metal passthrough, so containerised GPU stages would lose MPS and fall
back to CPU. Isolation is process-level instead.

**There is no GPU-core control, and that is not an omission.** Metal exposes no
way to partition the GPU between processes — no `CUDA_VISIBLE_DEVICES`, no MIG.
The Compute tab therefore offers the levers that are real: memory ceiling, VRAM
policy, CPU threads and workers, batch size, and the compute-shaped knobs
(steps, resolution). GPU load is controlled by how much work you send it.

---

## Configuration

Every knob is documented inline in `library/configs/knight_attack.yaml`. The ones that
are easy to get wrong:

| Setting | Note |
|---|---|
| `canonical.seed` | Pin it. Frames reuse it; changing it changes the character |
| `frames.controlnet.end_percent` | **0.2.** Holding control to 1.0 nails the pose but flattens the pixel style |
| `frames.ip_adapter.weight` | 0.3–0.5 style · 0.6 mixed · 0.8–1.0 identity lock |
| `frames.cfg` | 1.5 with LCM. 7 with LCM burns the image |
| `palette.factor` | 8. Verified as this LoRA's true grid by variance sweep |
| `palette.reduce` | `median` — 100% structural accuracy vs 70% for `mode` |

Note that "how faithful vs how creative" is **four** separate dials, not one:
`llm.temperature` for text, `cfg` for prompt adherence, `denoise` for how much
input survives, and the ControlNet/IP-Adapter weights for conditioning.
