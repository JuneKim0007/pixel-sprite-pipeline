# Measured decisions

Findings that cost GPU time or a bug to establish. Each says what was tried,
what was measured, and what follows. Read this before changing a default —
several of these defaults look arbitrary and are not.

Machine throughout: Apple M4, 16 GB unified memory, MPS.

---

## Generation

### `--gpu-only` is 4.9× slower here

ComfyUI's `--gpu-only` pins weights in VRAM. On unified memory there is no
separate VRAM to pin, so it fights the allocator instead of helping. Measured
4.9× slower end to end. Do not set it.

### LCM ignores pose control below ~12 steps

LCM's trajectory settles composition in the first couple of steps, and
ControlNet cannot redirect it afterwards. At 8 steps the skeleton is only
partially obeyed even at `end_percent` 0.85. At 20 steps the pose lands.

`end_percent` is a *fraction*, so it interacts with step count: 0.2 of 25 steps
is 5 steps and works; 0.2 of 8 LCM steps is 1.6 steps and is far too few.

### The Union ControlNet must be told its input type

It handles ten conditioning types in one model and defaults to guessing. Naming
the input as OpenPose rather than letting it guess is the difference between
the pose being applied and quietly ignored.

### Pose control at full strength makes the model trace the guide

Strength 1.0 held to `end_percent` 0.8 returned a stick figure — the model drew
the control image. Strength 0.75 to 0.55 lands the pose and leaves the LoRA room
to render a character over it. The negative prompt also names the failure
(`skeleton, bones, stick figure, wireframe, rainbow limbs`), which is what stops
the remainder.

### The skeleton channel hurts a standing pose

Measured on a standing character sheet: with the skeleton channel on, legs came
back as white shafts with ball joints — the guide drawn as bones. With depth
alone they came back as armoured legs with boots. A standing pose needs no
skeleton; an attack does.

### Illustrious XL, as configured, collapsed

Same seed, same prompt, same everything but `models.checkpoint`. SDXL base
produced a coherent pixel-art knight; Illustrious produced a blue-green cast
with crushed blacks and a cropped composition.

**That judgement was half wrong, and the half that was wrong matters more.**

The colour is broken. The *structure* is better, and measurably so. The two A/B
configs differ in exactly one field, so this is a clean comparison:

| | canvas | native block | native detail | factor | sprite | reduction |
|---|---|---|---|---|---|---|
| Illustrious | 1024 | **4 px** | 256 px | 8 | 128 px | **2.0 : 1** |
| SDXL | 1024 | 2 px | 512 px | 8 | 128 px | 4.0 : 1 |

Illustrious draws in blocks twice as large. Same LoRA at the same strength,
same seed, same prompt — the checkpoint's own feature scale differs.

That ratio in the last column is the thing to watch, and it had not been named
before. "Native detail" is how many logical pixels the model actually drew;
"reduction" is how many of them get averaged into one output pixel. At 2 : 1
each output pixel is a decision between four inputs. At 4 : 1 it is an average
of sixteen, and at 5 : 1 — which is where a 1280 canvas at factor 10 lands —
twenty-five. Averaging twenty-five hand-sized details into one pixel is what
"the dots look broken" is: it is not noise being added, it is structure being
destroyed before it can be quantised.

This is why the chunkier, flatter look is hard to reach on SDXL by tuning the
reduction. You cannot make the reduction gentler without either shrinking the
canvas (SDXL degrades below 1024) or accepting a larger sprite. The lever that
would actually work is making the model draw bigger blocks, which is what a
different checkpoint or a stronger pixel LoRA does.

**The colour failure is still real and still unexplained.** Three candidates,
none tested, ranked by likelihood:

1. **CFG.** Anime finetunes usually want 4–6 where SDXL base wants 7–7.5, and
   at 7.5 they oversaturate and crush blacks — which is exactly the symptom.
   Free to test.
2. **Prompt idiom.** Illustrious is trained on booru tags; natural language is
   out of distribution for it.
3. **VAE.** Least likely — Illustrious is SDXL architecture and the fp16-fix
   VAE should decode it — but `models.vae` exists to rule it out.

**SDXL base remains the default until that test happens.** But the reason to
run it is now stronger than "the other one collapsed": one of these two draws
at the feature scale this project is trying to reach, and it is not the one
currently selected.

### Batched candidates beat sequential ones here, and the first diagnosis was wrong

A four-candidate canonical died on a 1800 s timeout. That was read as the
working set exceeding 16 GB and macOS swapping — the `--gpu-only` story again —
and sequential generation was written to fix it.

Measuring the two paths against each other says otherwise:

| | wall | swapins |
|---|---|---|
| sequential | 2096 s | 2,809,129 |
| batch | **1806 s** | **1,281,996** |

Batch is 14% faster and swaps less than half as much. The original failure was
not swap thrashing: batch takes 1806 s and the timeout was 1800 s, so it missed
by six seconds. The correct fix was the timeout, which is now four hours.

Sequential is kept as an option because it is the only path that can rest
between candidates, which an overnight thermal-limited queue wants.

**The lesson is the one this file exists for.** A plausible mechanism, a
matching prior failure, and a real symptom produced a confident wrong diagnosis.
Nothing about it was checkable until both paths were run.

### Batching changes nothing about the images, and that is provable

The two paths were compared per candidate:

| | sequential vs batch |
|---|---|
| candidate 0 | **byte-identical** |
| candidates 1–3 | completely different |

Candidate 0 matching to the byte is the whole answer. If batching coupled its
samples in any way, the same seed in a batch of four could not produce exactly
the image it produces alone.

It cannot, because nothing in a diffusion UNet crosses the batch dimension.
Convolutions slide within one image. Self-attention attends over one image's
spatial positions; cross-attention attends to the text embedding. And SDXL
normalises with **GroupNorm, not BatchNorm** — BatchNorm would couple samples,
since it normalises using statistics computed across the batch, and that is the
classic case where batch size changes results. Diffusion architectures use
GroupNorm precisely so it does not.

So batching is an execution strategy, not a different computation. The only
thing that differs is which noise each slot receives: sequential steps the seed
explicitly (90210, 90211, …), while a batch derives four noises from one seed by
advancing its generator. Different samples, identical distribution.

Measured diversity confirms it — mean pairwise difference 62.8 sequential
against 65.6 batched, inside the spread of either — and mean gradient magnitude
is 2.59 for both, to two decimal places.

**Why batch is nevertheless faster** is the part that reverses the original
guess. The pressure was assumed to be four latents resident at once. A latent at
1024 is small; the checkpoint is 6.9 GB. Four separate graph executions read
those weights from memory four times, and one batched pass reads them once for
all four images. On unified memory, where bandwidth is the constraint rather
than capacity, that is the dominant term — and it is the likely reason the
sequential path recorded *more* than twice the swapins.

---

## Identity and references

### References are typed, and the weights differ by an order of magnitude

| role | what it says | base weight |
|---|---|---|
| identity | who the character is | 0.80 |
| style | what the art should look like | 0.35 |
| pose | a composition to reproduce | 1.00 |
| palette | colours to lock to | 1.00 |

A style reference at identity strength overwrites the character with the
exemplar. That order-of-magnitude gap is why they cannot share one list, and
the flat `references.images` key now raises rather than being silently
reinterpreted.

### Reference weight falls off with angular distance

The instinct is the opposite. A front reference forced at full strength onto a
rear generation produces a front-facing sprite fighting the pose; a weak hint
leaves the model free to invent the side it has never seen. Constraint where
there is evidence, latitude where there is not.

### No img2img from an identity reference

Identity references are usually illustrations, and denoising from one traces its
rendering — gradients, soft edges, anti-aliasing — which is the opposite of a
sprite. Identity goes through IP-Adapter only; the pixelation comes from
generation. `comfy.encode_image` is kept for the illustrate → pixelise pass,
which is a different thing: there the source is already in the target style and
tracing it is the point.

### The canonical anchors every frame, *and* stacks with the reference

The consistency recipe is that only one input varies:

    same seed, same prompt, same anchor, DIFFERENT skeleton

The frames stage used to use the canonical only as a fallback when no identity
reference existed — so supplying a reference silently removed the anchor the
stage cannot work without. Every frame was steered by one front-facing drawing,
and rear views got it at falloff weight with nothing holding them together.

They stack now. The canonical is the consistency anchor (already pixel art,
byte-identical across frames); the illustration is the detail source (a face and
a costume at a fidelity the canonical cannot hold at sprite resolution).

### The anchor's viewing angle fell through to a hardcoded default

`canonical.view` → `pose.view` → `"side"`. A character sheet lists its views in
`pose.set` and never sets `pose.view`, so the anchor was rendered in profile for
a front-facing sheet — and the front reference was then measured 90° away and
down-weighted for being a poor match. It was a poor match only because the
target had been chosen wrongly. It now falls through to `pose.set[0].view`.

---

## The reference pose

Both extremes were tried and both failed, measured on the humanoid rig as the
hand's lateral distance in hip-widths:

| pose | arm angle | hand clearance | failure |
|---|---|---|---|
| arms down | 4° | 2.00× | arms lie against the torso; the silhouette has no gap and neither a person nor a model can see where the arm ends |
| **A-pose** | **40°** | **5.42×** | — |
| true T | 88° | 7.55× | a long horizontal at shoulder height; a prompt naming a sword comes back with the blade drawn along an arm |

40° is what every character pipeline settles on, and now the default.

Two bugs surfaced while fixing this, both from moving joints without rotating
them: placing each joint along a ray from the shoulder preserved shoulder-to-
joint distance and silently rescaled the forearm, and the symmetric stance
assigned the ankle's `x` outright, stretching the shin from 0.1600 to 0.1607
every time a symmetric sheet was generated. Limbs are rotated rigidly now, and
a test checks every bone of every humanoid variant in both symmetry modes.

### Front is not the most legible view for every rig

Joint pairs landing within 4% of the canvas of each other, face joints excluded:

| rig | front | 3/4 | side |
|---|---|---|---|
| centipede | 74 | 27 | **19** |
| spider | 37 | 27 | **16** |
| serpent | 24 | 7 | **0** |
| scorpion | 21 | **6** | 11 |
| dragon | 15 | **1** | 13 |
| quadruped | 15 | **0** | 10 |
| humanoid | **0** | **0** | 8 |

A serpent seen head-on is a circle. The default view set for a character sheet
should be rig-aware — 3/4 for quadrupeds and dragons, side for serpents and
arthropods. **Not yet implemented.**

---

## Pixelisation

### Feature scale is the constraint; colour count is not

Colour count was the first thing measured and it was the wrong quantity. Palette
snapping runs after generation and is deterministic, so drifted colour comes
back. What does not come back is feature scale: reducing by eight destroys
detail that was one pixel wide and preserves detail already eight pixels wide,
and no post-process recovers the destroyed half.

`training.estimate_block_size` measures it the way `find_phase` measures the
grid's origin — average into blocks at each candidate factor, expand back, and
take the largest factor whose reconstruction error stays under 2% of the image's
own variance. On a staged set that colour count had flagged as a 17× problem,
feature scale read 1px for five images and 2px for two: a 2× spread, which is
fine.

### Median cut produces palettes with no value range

On a generated knight, median cut's eight entries had luminances
52, 144, 145, 145, 145, 145, 148, 227 — five of eight within four steps of each
other. It subdivides the RGB cube along its widest axis, so it spent the palette
separating hues inside one midtone. Clustering in the chosen metric's space
spread the same eight across 10–250.

### "Nearest colour" is a choice

| method | when |
|---|---|
| `rgb` | what earlier outputs used; perceptually the worst |
| `weighted` | luminance-weighted euclidean. Free, fixes most of it. Default. |
| `luma` | brightness first, hue as tiebreaker. **For sprites**: a sprite reads by its value structure, so an entry of the wrong lightness collapses the form even when the hue is right. Use when remapping between unrelated palettes. |
| `lab` | perceptually uniform, most faithful, ~10× the cost |

On a mid grey, a skin tone and a dark blue against a 136-entry palette, the four
disagreed on two of three.

### Curves belong before quantisation

Snapping is a nearest-neighbour decision, so what the values look like
beforehand decides which entries get chosen at all. Lifting contrast first
pushes midtones toward the ends of the ramp and the sprite takes up its light
and dark entries instead of collapsing into the middle ones. The same adjustment
afterwards just moves colours off the palette again.

### Background keying needs several passes

Generated sprites often arrive on a two-tone backdrop. A single flood removes
only the colour the corners sit on, and the surviving panel — 39% of the canvas
in the measured case — was counted as subject, which also polluted the palette,
since it is extracted from the same mask.

### Palette size is detail

At 12 colours a silver-armoured sprite collapsed to two greys and read as
monochrome. Reference art of this class runs 20–40. Once the shapes are flat,
colour count *is* detail.

---

## Training

### There is one model, not several

A character sheet and an animation share a checkpoint and a style LoRA. What
differs is the conditioning — rig, depth, ControlNet — not the weights. So
"training data for the character sheet model" describes a split that does not
exist, and building folders around it would divide one dataset into two halves
that each fail for lack of size.

The real split is: **style** (the rendering vocabulary — this is the LoRA),
**view coverage** (a LoRA can only render angles it has seen), and **nothing
for pose**, which comes from the rig and the depth map.

### A LoRA learns what is constant and cancels what varies

Nine images of a figure standing front-on with its arms down teach "standing,
front-on, arms down" as surely as they teach the outline, and the result fights
the ControlNet forever after: the pose lands, roughly, and then the weights drag
proportions and framing back toward the only composition they know.

Two independent levers keep composition out — vary it in the dataset, or name it
in the caption. **Caption what you want to control; omit what you want to
learn.**

### Self-training adds consistency, not capability

Training on your own outputs is a feedback loop: it locks in a look you already
reach sometimes, and cannot teach one you never reach. Internet images add real
capability. If the complaint is "this style doesn't come out", the dataset has
to come from outside.

### Feasibility on this machine

SDXL LoRA training on M4 / 16 GB is possible with kohya's fused backward pass;
Draw Things fine-tunes SDXL on Apple Silicon at ~10.3 GiB peak. Batch 1 and
conservative resolution. 20 usable images minimum, 40 comfortable — a style LoRA
saturates early. **Not yet set up.**

---

## Operations

### The queue must distinguish "broken" from "not ready yet"

Every stage checks whether ComfyUI is reachable and gives up in about a
millisecond when it is not, so a loop that caught errors and moved on would mark
two hundred jobs failed in under a second if the GPU service died overnight. A
job that cannot work fails immediately; a job that is merely early is held; a
service that is down pauses the worker and blames no job. This survived a real
ComfyUI outage with 200 jobs queued.

### Preflight needs the stage registry imported

The anti-cascade guard was itself a cascade: `preflight` never imported the
stage registry, so every job failed validation. `from . import stages` is
load-bearing wherever it appears.

### The autopilot cannot see aesthetic failure

It detects crashes, missing dependencies, dead services and invalid configs. It
does not detect two hundred jobs producing consistently ugly sprites — those
complete successfully. No online agent is required to *run* the queue; judgment
is still required to decide it is worth running.

### The editor rebooting the laptop was macOS doing it on purpose

Four reboots on 2026-08-13, each a ~30 second freeze and then a restart with the
windows restored. It reads like a crash and is not one. From
`/Library/Logs/DiagnosticReports/Retired/panic-full-2026-08-13-103229.panic`:

    panic(cpu 0, caller 0xfffffe003fd1fbc0): userspace watchdog timeout:
    no successful checkins from WindowServer (2 induced crashes) in 120 seconds

`watchdogd` requires WindowServer to check in. When it stops, watchdogd kills and
restarts it — the "2 induced crashes" — and if check-ins do not resume within 120
seconds it panics the kernel deliberately. So the reboot is a designed response
to a starved compositor, not a fault in this program. Nothing here crashed, and
nothing here could have caught anything: **the process that dies is never the
process at fault.** That is why ten fixes aimed at the Python process missed.

What starved it, from `JetsamEvent-2026-08-09-194451.ips`:

    largestProcess: python3.12
      14.94 GB  'python3.12'  pid=77424  prio=180  states=['active']
       0.65 GB  'WindowServer'            prio=170

14.94 GB of 16 GB. Note the jetsam priorities: the runaway process sits *above*
WindowServer, so the memory killer protects it and starves the compositor.

The six WindowServer watchdog hangs that day separate cleanly by free memory —
0.10 and 0.15 GB free both panicked; 3.03 and 3.39 GB free both recovered.

The 15 GB is arithmetic, not a leak: a 4096 px source at the declared maximum
zoom of 16 is 65536² × 4 bytes = 16.00 GB, against 14.94 GB observed. Four
independent defects combined to allow it, and each is worth stating because each
looked fine on its own:

- `Field(min=1, max=16)` was serialised to the browser and never enforced on the
  server, so the API accepted any zoom at all. A request is not a form.
- the budget guard checked what *entered* a layer, never what *left* one — so
  Scale, the only layer that multiplies size, was structurally exempt from the
  one check in the system.
- `np.repeat(np.repeat(...))` holds the n× intermediate and the n² result at once.
- the result came back as a base64 data URI inside JSON: six live copies of the
  output between the array and the socket.

And `memory_share` in `shared/limits.py`, which reads like a cap on the process,
only ever sized one numpy chunk. The 14.94 GB obeyed it exactly.

### Magnification is the viewer's job, and it always was

Every result surface carries `image-rendering: pixelated` — which *is*
nearest-neighbour magnification. The server was computing the same magnification,
encoding it, base64-ing it and shipping it, for a picture the browser then scaled
back down with `max-width: 100%` to fit a panel about 500 CSS pixels wide. At
zoom 16 a 384 px preview is 38 megapixels of thrown-away work.

So Scale is now *deferred*: the server sends the unmagnified image and the zoom it
owes, and the `<img>` carries the size. This is lossless rather than approximate,
and the codebase already contained the proof twice — repetition invents no detail
and cannot introduce a colour, which is why `_scale` counts colours before
magnifying and why `count_colours` strides.

For the written file the pixels are genuinely needed, so `definitive/pngstream.py`
encodes PNG a scanline at a time: output row r is source row r // n with each
pixel repeated n times, built and forgotten before row r+1 exists. Measured on a
512 px RGBA source at zoom 16 — 2.89 MB peak streamed against 272.00 MB
materialised, 94x — and the output verified identical to
`np.repeat(np.repeat(...))`. Peak does not grow with height at all, which is the
property that makes it a bound. Writing a real 16384² file peaked at 411 MB
instead of 1 GB for the array plus 6 GB of copies.

An unexpected second win: filter 0 on already-magnified data beat Pillow's
adaptive filtering on size, 49.8 KB against 90.7 KB for a 128 px sprite at zoom 8,
because magnification lengthens exactly the runs zlib is best at and a filter that
subtracts neighbours breaks them up.

### A limit a process sets on itself cannot save the machine

`shared/guard.py` watches from outside and its only action is SIGKILL, because
self-restraint fails three ways here and all three happened: it is too late (an
allocation is only observable once made), it is not universal (ComfyUI and the
generation subprocess have their own allocators and `limits.py` deliberately
leaves them alone — they are the ones that reach 15 GB), and it does not measure
the thing that fails, which is the machine and not the process.

Two signals: resident memory per process, and
`kern.memorystatus_vm_pressure_level` — the same subsystem jetsam uses, so it is
the machine's own opinion rather than ours. Sustained critical pressure kills the
largest watched process; a spike does not, because pressure spikes during
ordinary things and killing a render for one is a worse bug than the one this
fixes. ComfyUI is marked `expected_large` and is exempt from the per-process
ceiling: holding a diffusion model resident is its job, and at that point being
the biggest is exactly what makes it the right one to kill.

macOS has no cgroups, and `RLIMIT_AS` is unreliable under Metal — MPS reserves
address space far beyond its resident set, so a limit low enough to matter
refuses allocations that would have been fine. Polling RSS from outside is cruder
and actually works.

Killing is blunt on purpose. The alternative to a killed render is not a
completed render; it is a reboot that loses every unsaved thing on the desktop.
