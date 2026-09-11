# What is still open

Work that is known, named and deliberately not done. Each entry says what the
state actually is, what it would take, and why it has not been taken — so the
next person does not rediscover it, and does not start it thinking it is
smaller than it is.

Anything finished leaves this file and lands in the commit that closed it.
Measurements here are dated; re-measure before trusting one.

---

## 1. How work ARRIVES at a node is still two things

A layer is handed `inputs`; a stage is handed the run's `Context`. One
`apply(inputs, cfg, prep)` means what a stage reads off `Context` arrives as
inputs instead, which rewrites seven stage bodies.

The surface is nine members, not five. Counted 2026-09-02 across the seven
stages: `settings` 29 accesses, `need` 17, `config` 17, `root` 12, `require` 10,
`artifacts` 7, `stage_dir` 7, `run_id` 5, `outdir` 1 — 105 in all.

**Why not yet — corrected 2026-09-02.** This entry used to say the work could
not be verified without ComfyUI and a GPU. That was wrong on measurement and is
no longer the reason.

Only two of the seven stages are `Resource.GPU`. The other five compute from
files on disk and run with no ComfyUI and no GPU at all. All seven bodies now
execute under test: `canonical` and `frames` through a recording ComfyUI client
that byte-compares the graph they build across 24 scenarios — the graph *is* the
request, so that is not weaker than comparing a render — and the remaining five
directly.

The real obstacle is that two of the nine members are behaviour, not values.
`ctx.need` resolves lazily and memoises *because* a rig under `rig: auto` costs
an LLM call; handing a stage pre-resolved inputs resolves it for stages that
never ask. `ctx.stage_dir` creates the directory and assigns its `NN_` number as
a side effect of being called. Passing either one in moves *when* the work
happens, which makes this a design change rather than a refactor — and it is the
question to settle before the rewrite, not during it.

Blast radius, measured the same day: 105 access sites across seven stage files,
plus `stage.py`, `runner.py` and `resources.py`, plus nine test files that build
a `Context` and five that call `run(ctx, prep)`.

**Not worth doing at all: merging the two schedulers.** Measured 2026-08-27 —
the shared execution core is four lines, `prepare` then `apply`. Everything
around it differs for real reasons: serial versus `ThreadPoolExecutor` batched
by `Resource`; one image threaded versus a dict of artifacts; prefix snapshots
versus a manifest; a failing node recorded versus a run aborted; deferral and a
budget token versus cooling and `stop_after`. `NODES.md` §4's `Plan.batches()`
would merge two schedulers that then branch on every one of those.

Design: `docs/NODES.md` §4, read with this note.

## 2. `Context` is still reachable from inside a stage

A stage declares `needs` and reads `ctx.need("rig")`, so the dependency is
checked before the run. But because it holds `ctx`, nothing stops it reading a
resource it never declared. Only passing resources as arguments makes that
impossible rather than merely checkable — which is item 1.

## 3. `schema.FIELDS` is a parallel list — and should stay one

`NODES.md` §6.1 asks for it to become a projection of the nodes: each `Stage`
owning its fields, `FIELDS` assembled from the registry. **Do not do this.**
It requires `schema` to read the stage registry, which re-creates the
`schema ↔ stage` cycle broken on 2026-08-26 — and as an *intra-group* cycle,
the packaging test cannot catch it coming back.

Measured 2026-08-27: nothing is currently wrong. 91 of 137 fields sit under a
stage prefix and 46 under other config blocks; no stage lacks fields, and no
`settings()` path in the tree lacks a declaration. The relationship is correct,
it was simply unenforced.

**A precedent, 2026-09-08.** Asset types needed the same join — "is every stage
this type names registered" — and took the other route: the registry lives in
`shared/modules.py`, which `test_packaging.py:27` forbids from importing any
sibling group, so it *cannot* ask the stage registry anything. The join is
forced into `api/machine.py`, beside the one that already fills
`options.stage_names`. Placing the data where the cycle is impossible costs
nothing and needs no test; that is the shape to copy if this entry is ever
revisited.

So it is enforced instead of restructured. Two checks in
`tests/unit/test_contracts.py`: every `settings("path")` found by walking the
AST has a field declared at or under it, and every field prefix names either a
stage, a block something reads through `settings()`, or one of three blocks
read another way — `cooling` through `cooling.rest`, `detect` inside
`refs/detect.py`, `pipeline` from `run.py` before a `Context` exists.

**Still true, and not covered:** a field whose block nothing reads at all. The
reverse check is all false positives, because several blocks are read through
raw config, the queue, or `ctl.sh` rather than `settings()`.

## 4. Two model settings have no control

**Measured 2026-09-08.** `models.lcm_lora` and `models.clip_vision` sit in
`DEFAULT_GLOBAL` and are read while the graph is built — `comfy.py:230` and
`comfy.py:251` — and neither has a `ConfigField`. Every other `models.*` entry
renders in the settings form, so `_global.yaml` is the only way to move these
two.

Reachability was the other half of this and is settled: `apply_ipadapter` built
`CLIPVisionLoader` from `DEFAULT_GLOBAL` directly, so the setting was declared,
documented and ignored; since 2026-09-09 it resolves both weights through
`model_name` off the `models` block it is passed.

**Why not.** Inventing controls for the two is a product decision, not a
cleanup.

## 5. The pixelise pass is wired up and unmeasured

**Wired 2026-09-11.** The three pieces the old entry named - `encode_image`
with no caller, `sample_and_save`'s unused `latent`, and a `denoise` every
config left at 1.0 - are one img2img path, and they are connected now by a
`pixelise` stage between `canonical` and `frames`.

**What it is for.** `estimate_block_size` on what the model draws reads 1.75 to
2.00 on a 1024 canvas where a 128 sprite wants 8, measured across 24 runs, and
no conditioning moved it: char8 reads 2.0 under baseline, no_key and
identity_led alike. What the model will not draw it can be handed. The stage
quantises the canonical to the sprite's own grid and back, then samples from
that latent, so the re-render traces a block structure instead of inventing a
finer one. Verified on a real canonical: block 2.0 in, 8.0 out, 128x128 cells,
canvas unchanged.

`DECISIONS.md` argues against img2img from an identity reference because
denoising from an illustration traces its gradients. It keeps `encode_image`
for exactly this case - the source is already in the target style and tracing
it is the point - and the source here is the model's own canonical.

Frames prefer `pixel_anchor` over `canonical` when the stage has run, because a
frame inherits its block from what it is anchored to.

**What is left, and it is the whole question.** No generation has run through
it. The quantiser is measured and the wiring is tested; whether sampling at
denoise 0.45 keeps the 8px grid or melts back to 2 is unknown, and that is the
only thing that decides whether the pass is worth its GPU minute. `pixelise` is
in no config's `pipeline.stages` until it is.

## 6. The stylelog writes half of what it reads

`views/styles/styles.js` renders `train` and `tune` events;
`looks/stylelog.py` has `tune_event`, `train_event` and `archive_training` and
nothing calls them. A half-built feature, not dead code — deleting the writers
would leave live readers for a format nothing can produce.

**Why not.** Finishing or removing it is a product call.

## 7. Ten raw `ctx.config` reads remain

Down from 28. Each remaining one is either not a schema field (`props`,
`paths.*`) or wants a different fallback than the schema would give —
`subject` and `style`, where `canonical` and `frames` fall back to a prompt
string and the LLM palette chooser deliberately passes an empty one.

Reasoning: `docs/CONFIGURING.md`, "The four settings with no default".

---

The entries below were opened 2026-09-10, from one session of using the editor
and the run wizard. Several are things the code does that nobody asked it to;
those say so rather than being written up as features.

## 8. Only one asset type says what its settings start from

**Surveyed 2026-09-10.** `ModuleSpec` carries `defaults`, and precedence is
config, then asset type, then field. One type declares one entry:
`character_sheet` with `pose.source: tpose`.

Other per-type facts are still asserted globally or restated in every config —
all three `animation` configs set `pose.source: library` by hand, which is what
the declaration exists to stop. Worth a survey the next time one of them bites,
not a speculative sweep now.

## 9. Three primitives with no caller

**Re-counted 2026-09-11.** `Mono`, `LabelWithTip` and `BaseCard` have no use
outside `web/js/ui/`. `Note`, `Check`, `Fact` and `FactGrid` were on this list
and have callers now.

Each needs the check `Section` failed - does its CSS match what a view actually
needs - before it is adopted or deleted. Do not sweep them as a batch; that is
how `Section` came to be documented as a bordered container without anyone
reading the rule.

## 10. The prompt cannot choose a backdrop, and now does not have to

**Measured 2026-09-10, worked around 2026-09-11.** A run asking for magenta by
name, with nothing contradicting it, produced a pale blue-grey card: 0.0%
near-magenta at tolerance 60, corners (191, 208, 218). Three corrections to the
ask changed nothing about the answer. What the sprite sits on matches the style
exemplars, which IPAdapter carries along with the style, with no text to argue
with.

`background.colour: auto` is the default now: the prompt still names magenta,
and the keyer reads the corners of what the model actually painted. Turning the
backdrop off instead was measured and is worse - it drops the ground shadow but
leaves bleed at 0.1295 against 0.1316.

**What is left.** The prompt still cannot *choose* the colour, so a run that
needs a specific backdrop has no lever. Restricting `canonical.style.end_at` so
the backdrop is decided after style transfer stops is the untried option, and
it is a config edit.

## 11. Whether a sheet's rear view is better for naming the prop is unmeasured

**Open since 2026-09-10.** `props.wanted` is geometry and `props.named` is
words, so a `character_sheet` names what the character carries without being
given a socket that cannot be placed on a static multi-angle render. Before the
split the only channel was `subject`, asserted identically in all four views
including the rear.

The 12:22 run does not answer whether the rear view gains anything: its
config.yaml was snapshotted before the archers were cleaned, so it carries the
old subject clause and the props words and asks for the bow twice, which makes
it the wrong control. The next clean run is the one to look at.

## 12. A painted emphasis map applies to identity, not to style or pose

**Consumed 2026-09-10; what is left is narrower than the entry it replaces.**
A map painted on a reference now reaches the graph as `IPAdapterAdvanced`'s
`attn_mask`, on the identity adapter in both `canonical` and `frames`.

It went in there rather than through `ConditioningSetMask` because the mask is
not applied to the prompt at all: `CrossAttentionPatch.ipadapter_attention`
interpolates it to the latent attention grid and multiplies it into `out_ip`,
that adapter's own contribution. So it says where a REFERENCE steers, which is
what was painted, and it needs no second conditioning branch and cannot seam
two disagreeing regions against each other.

The size was already right by accident: SDXL at 1024px has a 128x128 latent
grid, and the painter writes 128x128, so nothing is resampled.

**What is left.** Style exemplars and the anchor take no mask - both call
`apply_ipadapter` without one, so a map painted on a style sheet is still
inert. Whether style SHOULD be regional is a real question and not obviously
yes: an exemplar is meant to tint the whole figure. The pose ControlNet is
separate again and would need `ConditioningSetMask`, with the seam risk that
kept it out of this change.

Unmeasured: whether a mask on identity actually changes an output, and by how
much. The wiring is tested; the effect is not.

## 13. The outline `retro_jrpg` asks for is drawn by the steps style transfer owns

**Untried since 2026-09-10.** `retro_jrpg` asks the prompt for a "thick dark
outline around the whole figure" while the style exemplar votes to 0.8 of
sampling, which covers the steps that draw linework. Lowering
`canonical.style.end_at` hands those steps back to the prompt.

`canonical.style.end_at` is a declared field now - one of four that render
beside the rig canvas, each defaulting to exactly the literal it replaced - so
this is a slider and a look at the result rather than a code change.

## 14. A broad body and a broad face are one dial in the depth map

**Rewritten 2026-09-11; the entry it replaces was wrong.** It said no lever
made a body broader and asked for a tenth proportion group. Two levers already
existed, and one of them hits the reference exactly:

| | h/shoulder | head/shoulder |
|---|---|---|
| reference sheet, front figure | **4.42** | |
| humanoid neutral | 6.26 | 0.545 |
| `pose.lateral_scale: 1.4` | **4.47** | 0.545 |
| `pose.spread: {arms: 1.4, torso: 1.4}` | **4.47** | **0.390** |

`lateral_scale` is a uniform horizontal scale - head-over-shoulder is identical
at 1.0 and 1.4 - so it broadens the shoulders and the face together. That is
the whole of what was missing, and `pose.spread` is now the per-group form,
shaped like `depth.build` because it answers the same shape of question. The
two are halves of one idea: spread moves the joints apart, build thickens the
limb between them.

`depth.build` was itself unusable until 2026-09-10: `Field.clamp` coerced
before it bounded, a mapping cannot coerce to a float, and the failure path
returned the field's default of None. Eighteen runs set it and none applied it,
which is most of why this entry read as "no lever exists".

**What is left.** Nothing in the pipeline sets `pose.spread` yet, so no shipped
config is broader than it was; the numbers above are measured off the rig, not
off a generation. Whether 1.4 survives the model - ControlNet stops steering at
`canonical.controlnet.end_percent` and the figure drifts after that - is
unmeasured.

## 15. Eighteen comment blocks still over the limit

**Swept 2026-09-10, re-counted 2026-09-11.** The rule is one line per comment
and two per docstring. The sweep took 238 blocks over the limit down to 18, all
in `web/app.css` and `tests/frontend/`, which neither pass covered.

`make check` does not refuse a new one, so this can drift back. That check is
the part worth doing, not the last eighteen.

## 16. "weight 1.00" on a reference card is not the weight

**Found 2026-09-10.** The number on each reference card is `weight_scale`, a
multiplier. The weight IPAdapter actually receives is decided per frame by
`references.pick()` from the angle between the frame and the reference:

```
weight = exact_weight ... far_weight   interpolated over the angle
         0.85            0.45          beyond references.match.tolerance (40°)
applied  = weight * ref.weight_scale
```

So a card reading `1.00` produces 0.85 on a matching frame and 0.45 on the
opposite one, and the card says neither. `1.00` is the neutral multiplier, not
"full trust", which is why it is allowed above 1 - the identity slider ranges to
1.5 and there is no clamp on the parsed value at all (`_one()` takes whatever
the YAML says; only `style_weight()` clamps, at 0.6).

Nothing is normalised across the three cards because nothing sums: `pick()`
selects exactly one reference per frame by nearest yaw and the others are not
in the graph for that frame. Normalising to 1.0 across three views would make
each view weaker for the crime of supplying more views, which inverts what more
views are for.

What it would take: show the applied range on the card, not the multiplier -
`0.85 front / 0.45 rear` beside a slider that keeps calling itself a
multiplier. Same fix reaches the missing clamp, since the range is what makes
2.55 visibly wrong.

The control is also a bare `<input type=range>` rather than the `Range`
primitive, so it is one of the stragglers from the slider sweep.

## 17. The latest output is shown but is not part of the history

**Asked 2026-09-10.** The overview's third column shows the newest run's frames
under "LATEST OUTPUT" with a "Refine in editor" button, and the styles view has
a History tab. The newest output does not appear in that history, so the one
view that answers "what did this produce" and the one that answers "what has
this produced over time" do not share a source. Whether that is one feed with a
newest-first cursor or two genuinely different questions is the thing to decide
before writing either.

## 18. Terms cannot be weighted in a prompt

**Asked 2026-09-10, unanswered.** Whether "gender = girl" or a skin term can be
made to count for more than the fragments around it. SDXL through ComfyUI
accepts `(term:1.3)` attention syntax in `CLIPTextEncode`, but whether this
graph's encoder path preserves it, and whether a weighted term survives the
IPAdapter and ControlNet conditioning that follow, is unmeasured. The style
vocabulary is a flat list of equals today.

## 19. Two hand-rolled segmented controls remain

**Counted 2026-09-11.** `Segmented` is in `ui/kit.js` and most callers use it.
Two build the markup by hand: `views/input/input.js` (reference role tabs) and
`views/styles/styles.js`.

Deliberately excluded, with reasons: `rail-cell`, `nav li`, `subnav-item` and
`step` look the same but are navigation, not a value control - they change what
is shown rather than what is set, and collapsing them would put a form
primitive in the chrome. `.seg` and `.segmented` also carry two different
radius tokens, which is the part worth settling first.

---

The entries below were measured 2026-09-11, from one pass tracing every
declared field to the place that reads it. The method is repeatable: wrap
`Context.settings` in a recording dict, run the suite, and diff what was read
against `SCHEMA.fields`. All 852 tests pass with every one of these present, so
the tests are not the thing that would have caught them.

## 20. The validator and the pipeline disagree about which settings exist

**Measured 2026-09-11.** `SCHEMA.check` is called from `api/configs.py` and
nowhere else. `run.py` never calls it. So a config is validated when it is
saved through the editor and not when it is run from the CLI, and ten paths the
pipeline reads are refused by the half that validates:

| path | read at | set by |
|---|---|---|
| `canonical.from_reference.weight_type` | `canonical.py:106` | `styles/base_pixel.yaml:96` |
| `canonical.from_reference.start_at` | `canonical.py:107` | — |
| `canonical.from_reference.enabled` | `canonical.py:186` | — |
| `canonical.prompt` | `canonical.py:173` | `configs/experiments/_illu_cfg{40,50,60}.yaml:34` |
| `frames.prompt` | `frames.py:82` | — |
| `pose.views` | `pose.py:109` | `PoseStage.DEFAULTS` |
| `detect.host` | `detect.py:95` | — |
| `detect.model` → `keep_alive` | `detect.py:97` | — |
| `detect.attempts` | `detect.py:106` | — |
| `palette.llm.*` | `palette.py:148` | — |

`pose.views` is the sharpest of them. `PoseStage.DEFAULTS` declares it,
`cfg["views"]` reads it, and `pose.py:188` raises an error whose text tells the
user to set it — while the validator answers `'pose.views' is not a setting
this pipeline has.`

`references.images` is refused too, but that one is correct by accident: it
exists only as a deprecation guard at `references.py:108` that explains the
move to typed roles. The cost is that the editor answers with the generic
"not a setting" instead of that explanation.

This is not the same problem as §3. §3 measured that no `settings()` *prefix*
lacks a declaration, which is still true; the gap is one level down, at the
leaves the block is then indexed with.

**What it would take.** Call `SCHEMA.check` in `run.py` as well, which will
fail immediately on `base_pixel.yaml` — that failure is the table above.
Then declare the ten, which is mostly mechanical: three of the four
`from_reference` siblings are already fields, and `detect.*` should probably
not exist at all (§24).

## 21. A declared default makes the consumption site's fallback dead code

**Measured 2026-09-11.** `Context.settings(block)` merges
`SCHEMA.defaults_under(block)` under the config before handing the dict over.
Once a field declares a default, the key is always present, so
`opt(block, "key", literal)` at the consumption site can never reach its
literal. Whichever default wins depends on whether the field happens to declare
one — which is a coin toss, not a rule.

It has already cost one hyperparameter. `palette.py:148` merges `pose.llm`
under `palette.llm` and then asks for a lower temperature for palette
selection:

```python
llm_cfg = {**(ctx.settings("pose").get("llm") or {}), **(cfg.get("llm") or {})}
temperature=opt(llm_cfg, "temperature", 0.4)
```

`pose.llm.temperature` declares `0.7`, so the merged dict already carries it
and the `0.4` never fires. Measured:

```
palette LLM block actually used: {'temperature': 0.7, 'model': 'qwen3:4b', ...}
  temperature the code asks for: 0.4
  temperature actually applied : 0.7
```

The same pattern is load-bearing and *correct* two files away, for the opposite
reason. `canonical.py:129` wants a different ControlNet strength per channel —
`0.55` for pose, `0.30` for depth — and gets it only because
`canonical.controlnet.strength` declares no default. Adding one to that field,
which looks like an improvement, silently collapses both channels onto it.

**What it would take.** A test over the AST: for every `opt(block, key, lit)`
and `block.get(key, lit)` whose `key` resolves to a declared field, assert the
field declares no default. It is the same shape as
`test_the_form_s_default_is_what_the_pipeline_reads`, run from the other end,
and it catches both the dead fallback and the drift. The pass that found this
took about thirty lines.

## 22. `detect.*` is a second LLM block that the settings stack never reaches

**Measured 2026-09-11.** `resources.py:45` passes raw `ctx.config` into
`detect.resolve`, and `detect.py:93` reads `config.get("detect")` directly. So
that block skips global defaults, module defaults and field defaults, and only
the clamp in `Context.__post_init__` touches it. It is a third copy of a
configuration shape that already exists twice — `pose.llm` declared, and the
`palette.llm` of §23 undeclared.

The concrete loss: the help on `pose.llm.keep_alive` says *"Read by pose.py,
palette.py and refs/detect.py."* `detect.py` does not read it. It reads
`detect.keep_alive`, which is undeclared and unsaveable (§22). So the one knob
that exists to stop a vision model sitting resident beside SDXL on a 16 GB
machine does not reach the vision model it was written for.

Second, smaller: `detect.model` and `detect.min_confidence` are scoped
`modules=["character_sheet"]`, but `_detected()` runs for every module. An
`animation` config with `rig: auto` consumes both while the form hides them.

**What it would take.** Either point `detect.py` at `ctx.settings("detect")`
and declare the three missing keys, or fold the block into `pose.llm` and
delete it. The second is the better shape and the larger change, because the
vision model and the text model are genuinely different models and `pose.llm`
would need to say which is which. This is also one of the ten reads counted in
§8, and the only one there that is a mistake rather than a deliberate
different fallback.

## 23. Six fields whose real default is a literal somewhere in the code

**Measured 2026-09-11.** A field with no declared default renders as an empty
row. That is honest when there is no default. For these six there is one, and
it lives at the consumption site:

| field | form shows | actually used | source |
|---|---|---|---|
| `canonical.style_weight` | blank | **0.35** | `DEFAULT_WEIGHT["style"]` |
| `frames.style_weight` | blank | **0.35** | same |
| `canonical.controlnet.strength` | blank | **0.55** pose / **0.30** depth | `canonical.py:129` |
| `canonical.controlnet.end_percent` | blank | **0.40** / **0.35** | `canonical.py:132` |
| `detect.model` | blank | `qwen2.5vl:3b` | `detect.py:96` |
| `frames.seed` | blank | `canonical.seed` | `frames.py:134` |

`canonical.style_weight`'s own help cites 0.18 and 0.35 as the values that
matter and declares neither, which is the entry arguing with itself.

The two `controlnet` rows cannot simply be filled in — that is the trap in
§23, and filling them is what collapses the per-channel split. They want the
declaration to carry the split, or the form to say "0.55 pose / 0.30 depth"
without pretending it is one number.

Related: nothing in the suite reads `style_weight` at all. The recording pass
never saw either field, because no test builds a run with a style exemplar
attached, so the whole `_with_style` path in `canonical.py:115` and
`frames.py:203` is untested — including the 0.6 clamp §18 mentions.

## 24. `proportions` is never empty, so the rig is always rebuilt

**Measured 2026-09-11.** All nine `PROPORTION_GROUPS` declare a default of
`1.0`, so `ctx.settings("proportions")` always returns nine entries and
`rigs.scale`'s `if not factors: return rig` can never be taken. Every run
rebuilds the rig to apply nine identity transforms, and every rig label comes
out as

```
Humanoid (arms x1, head x1, legs x1, neck x1, segments x1, tail x1,
          tentacles x1, torso x1, wings x1)
```

which is what every log line and every run record then carries. The rebuild
also converts each `neutral` value from a tuple to a list; the numbers are
identical and nothing currently depends on the type, which is why it has not
shown up.

`rigs.groups_of(rig)` — *"Proportion groups this rig actually has"* — is
called from nowhere. It is exactly the function that would stop a humanoid
config from offering Wing span, Tentacle length and Segment length, all three
of which the help text already admits are ignored. It is a §10-shaped
primitive with a caller waiting for it in `fields_for`, rather than one with
no use.

**What it would take.** Either drop the nine declared defaults and let the
absent key mean 1.0, or compare against 1.0 rather than truthiness before
rebuilding. The first is cleaner and changes what the form shows, so it is a
product call. Wiring `groups_of` into `fields_for` is independent of both, and
is the half worth doing first — §16 will add a tenth group and make the
unfiltered list worse.

