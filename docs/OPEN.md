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

## 5. The illustrate to pixelise pass is wired for and not wired up

Three pieces exist and nothing connects them: `comfy.encode_image` has no
caller, `sample_and_save` takes a `latent` nobody passes so every sample starts
from `EmptyLatentImage`, and `frames.denoise` is exposed as a setting that every
config in the library leaves at 1.0. Together they are one img2img path.

**Why they are here.** `DECISIONS.md` argues against img2img from an *identity*
reference — denoising from an illustration traces its gradients and soft edges,
which is the opposite of a sprite — and keeps `encode_image` for the different
case where the source is already in the target style and tracing it is the
point.

**Why this note exists.** That reasoning lives inside a decision about something
else, so the three pieces read as dead code to anyone who finds them first. They
are a capability that was never wired up, which AGENTS.md says not to delete.
Deleting `encode_image` also would not be caught: it is a module-level export,
so no linter reports it.

**What finishing it would take.** A caller that encodes a source image, passes
the latent to `sample_and_save`, and reads `denoise` below 1.0 — plus a decision
about which stage owns it, since neither `canonical` nor `frames` should grow a
second mode.

## 6. The stylelog writes half of what it reads

`views/styles/styles.js` renders `train` and `tune` events;
`looks/stylelog.py` has `tune_event`, `train_event` and `archive_training` and
nothing calls them. A half-built feature, not dead code — deleting the writers
would leave live readers for a format nothing can produce.

**Why not.** Finishing or removing it is a product call.

## 7. Ten write routes are declared but never driven

Every route carries a response contract, checked at import. Seven of seventeen
write routes are also driven against a live body. The other ten cannot be,
each for a stated reason: a subprocess (`POST /run`), a network fetch
(`POST /download`), an untransactional queue (`/queue/submit`, `/queue/job`),
a precondition that cannot be staged (`POST /stop`), multipart into
`input_dir()` (`POST /upload`), or a file the repository tracks
(`PUT /global`, the three `/style/*` writers).

The `http` fixture checks every call any test makes, so each is picked up free
the moment something exercises it.

Detail: `docs/superpowers/specs/2026-08-14-vertical-sweep-design.md` §5.

## 8. Ten raw `ctx.config` reads remain

Down from 28. Each remaining one is either not a schema field (`props`,
`paths.*`) or wants a different fallback than the schema would give —
`subject` and `style`, where `canonical` and `frames` fall back to a prompt
string and the LLM palette chooser deliberately passes an empty one.

Reasoning: `docs/CONFIGURING.md`, "The four settings with no default".

---

The entries below were opened 2026-09-10, from one session of using the editor
and the run wizard. Several are things the code does that nobody asked it to;
those say so rather than being written up as features.

## 9. Only one asset type says what its settings start from

**Surveyed 2026-09-10.** `ModuleSpec` carries `defaults`, and precedence is
config, then asset type, then field. One type declares one entry:
`character_sheet` with `pose.source: tpose`.

Other per-type facts are still asserted globally or restated in every config —
all three `animation` configs set `pose.source: library` by hand, which is what
the declaration exists to stop. Worth a survey the next time one of them bites,
not a speculative sweep now.

## 10. Four primitives with no caller, and a card base for a view that does not exist

**Checked one at a time 2026-09-10.** `Mono` takes its text at construction and
all three candidate spans are live readouts mutated later; it would work and it
would not remove anything. `Note`, `Check` and `LabelWithTip` are the same story
at smaller scale. `BaseCard` has no caller and nothing resembling one; whether
views want a card base is a question about a view that does not exist yet.

**This is a decision, not a task.** A primitive nothing needs is not
half-finished work. Each should either grow to fit what views actually build —
which is what happened to `Range`, adopted by four callers once it took a
readout — or be deleted. Both are product calls and neither is urgent. The
dead-CSS test already stops the rules from drifting again.

## 11. `background.colour` names a colour the model does not paint

**Measured 2026-09-10.** The clean run of 15:49 — one background asked for, by
name, with nothing contradicting it and no background words anywhere in the
style vocabulary — produced a pale blue-grey card. 0.0% near-magenta at
tolerance 60, corners (191, 208, 218). Three corrections to what was asked, and
no change in what came back, so the prompt is not the lever.

What the sprite actually sits on matches the `hi_fidelity` exemplars, which are
white, off-white and dark purple. IPAdapter style transfer runs at 0.0-0.8 of
sampling and carries the backdrop along with the style, applied per exemplar
with no text to argue with.

**What is worth trying, in order.** Restrict style transfer's end_at so the
backdrop is decided after it stops — `canonical.style.end_at` is a declared
field now, so that is a config edit and a look at the result. Or key on what the
model actually produces rather than on a colour it was asked for, which is §15.
§13's emphasis map is a third option and the most expensive.

**Not a bug in the keyer.** It removed 78-80% of both canonicals correctly, and
`background_to_alpha` floods from the corners, which is why the pipeline works
at all despite this. The gap is only that `background.colour` claims to name
what the model will paint, and it does not.

## 12. Whether a sheet's rear view is better for naming the prop is unmeasured

**Open since 2026-09-10.** `props.wanted` is geometry and `props.named` is
words, so a `character_sheet` names what the character carries without being
given a socket that cannot be placed on a static multi-angle render. Before the
split the only channel was `subject`, asserted identically in all four views
including the rear.

The 12:22 run does not answer whether the rear view gains anything: its
config.yaml was snapshotted before the archers were cleaned, so it carries the
old subject clause and the props words and asks for the bow twice, which makes
it the wrong control. The next clean run is the one to look at.

## 13. A painted emphasis map applies to identity, not to style or pose

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

## 14. The outline `retro_jrpg` asks for is drawn by the steps style transfer owns

**Untried since 2026-09-10.** `retro_jrpg` asks the prompt for a "thick dark
outline around the whole figure" while the style exemplar votes to 0.8 of
sampling, which covers the steps that draw linework. Lowering
`canonical.style.end_at` hands those steps back to the prompt.

`canonical.style.end_at` is a declared field now - one of four that render
beside the rig canvas, each defaulting to exactly the literal it replaced - so
this is a slider and a look at the result rather than a code change.

## 15. The keyer can be told a colour and cannot be asked to find one

**Found 2026-09-10.** `background.colour: auto`, sampling the canonical's own
corners. Three runs measured 0.0% near-magenta whatever the prompt asked, so the
colour the model actually produced is the only reliable key, and the corners are
where it is. Deterministic and exact; an LLM round for the same question would
return a name that needs parsing back to a number and can differ between runs.

Parsing is no longer part of this. `palette.py:_key_colour` took six hex digits
and nothing else until 2026-09-10, so `12, 34, 56` and `#abc` silently disabled
keying; it asks `shared.colour.parse_colour` now, which is what the Definitive
editor's background layer always used.

## 16. A broad body and a broad face are one dial in the depth map

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

## 17. Reasoning lives beside the code instead of in docs

**Measured 2026-09-10.** The rule is one line per comment, two per docstring;
anything longer belongs in `docs/` or the commit that made the decision.

| | blocks over the limit | lines |
|---|---|---|
| `web/js` comment blocks over 1 line | 144 | 796 |
| `pipeline` + `tests` docstrings over 2 lines | 57 | 321 |
| `pipeline` + `tests` comment blocks over 1 line | 27 | 74 |

Roughly 1200 lines. Most of the frontend's share is a file-header block on line
1 of nearly every view, and those carry design reasoning that is not written
down anywhere else - deleting them loses it, so each one is a move into
`docs/FRONTEND.md` or `docs/UI.md`, not a `sed`. The Python docstrings are the
same shape: `pixelize.py:65` is sixteen lines explaining a measurement.

What it would take: read each block, decide whether it states a decision (goes
to docs, with the date), restates the code (delete), or is the one line worth
keeping. Then a check in `make check` that refuses a new one, the way
`tools/check_failures.py` refuses a builtin raise, so the sweep does not have to
happen twice.

## 18. "weight 1.00" on a reference card is not the weight

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

## 19. The latest output is shown but is not part of the history

**Asked 2026-09-10.** The overview's third column shows the newest run's frames
under "LATEST OUTPUT" with a "Refine in editor" button, and the styles view has
a History tab. The newest output does not appear in that history, so the one
view that answers "what did this produce" and the one that answers "what has
this produced over time" do not share a source. Whether that is one feed with a
newest-first cursor or two genuinely different questions is the thing to decide
before writing either.

## 20. Terms cannot be weighted in a prompt

**Asked 2026-09-10, unanswered.** Whether "gender = girl" or a skin term can be
made to count for more than the fragments around it. SDXL through ComfyUI
accepts `(term:1.3)` attention syntax in `CLIPTextEncode`, but whether this
graph's encoder path preserves it, and whether a weighted term survives the
IPAdapter and ControlNet conditioning that follow, is unmeasured. The style
vocabulary is a flat list of equals today.

## 21. Five segmented controls, and one primitive none of them use

**Enumerated 2026-09-10, not converted.** `Segmented` exists in `ui/kit.js:141`.
Five controls build the same thing by hand: `queue.js` states, `result.js`
Grid/Anim/Strip, `run.js` Author/Annotate, `styles.js` tabs, `settings.js`
scope. A sixth appeared since: the reference role tabs in `input.js`.

Deliberately excluded, with reasons: `rail-cell`, `nav li`, `subnav-item` and
`step` look the same but are navigation, not a value control - they change what
is shown rather than what is set, and collapsing them would put a form
primitive in the chrome. `.seg` and `.segmented` also carry two different
radius tokens, which is the part worth settling first.
