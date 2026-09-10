# What is still open

Work that is known, named and deliberately not done. Each entry says what the
state actually is, what it would take, and why it has not been taken — so the
next person does not rediscover it, and does not start it thinking it is
smaller than it is.

Anything finished leaves this file and lands in the commit that closed it.
Measurements here are dated; re-measure before trusting one.

---

## 1. How work ARRIVES at a node is still two things

**Half done 2026-08-27.** What a node *returns* is one contract now: it answers
with a dict, and `plan.undeclared` holds both engines to it — a key it never
declared is refused, a key it declared and withheld is refused. The layer
engine had no such check at all; a layer wrote into a `facts` dict the caller
passed in, and any key or none went unnoticed.

**What is left.** A layer is handed `inputs`; a stage is handed the run's
`Context`. One `apply(inputs, cfg, prep)` means what a stage reads off `Context`
arrives as inputs instead, which rewrites seven stage bodies.

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

## 4. `DEFAULT_GLOBAL` holds three settings with no control, and one that a
control could not reach

Corrected 2026-09-08. This entry used to list seven settings and say the
settings form "has never offered them and still does not". That was already
wrong for the three `paths.*` entries — `settings.js` has rendered them through
a hand-written `pathsSection` for some time, and the Input tab rendered a second
copy of the same three. The duplicate is gone and `Settings -> Paths` is now the
one editor, scoped by what each folder is: `output_dir` is read from the run
config by `generation/resources.py` and `orchestration/queue.py`, so a pipeline
may pin it; `input_dir` and `download_dir` are only ever read through
`api/context.py` from the global, so pinning them did nothing at all.

`ui.suppress_gate_confirm` also has a control — the "don't show this again" box
in `views/run/run.js`.

**What actually has none.** `models.lcm_lora` and `models.clip_vision`.

`ui.suppress_overwrite_confirm` was the third and is now deleted rather than
given one. Wiring it was the obvious next step and the wrong one. The dialog it
named exists - `result.js` asks before a download clobbers files - but its
answer is not a preference, it is a decision per download: Overwrite replaces
them, Cancel keeps both by adding a numbered suffix. A remembered "Overwrite"
would silently clobber from then on. `suppress_gate_confirm` is safe to
remember because it only warns that a run will pause. A test now refuses any
`ui.*` flag whose name contains "overwrite".

**`models.clip_vision` was unreachable, and now is not.** Fixed 2026-09-09.
`apply_ipadapter` built `CLIPVisionLoader` from `DEFAULT_GLOBAL` directly while
taking `ipadapter` as a parameter, so the setting was declared, documented and
ignored — a form field for it would have changed nothing. It now takes the
whole `models` block and resolves both weights through `model_name`, which is
what `base_graph` beside it already did. Setting it in `_global.yaml` reaches
the graph; it still has no `ConfigField`, which is the product decision below.

**Why the rest are not done.** Inventing controls for them is a product
decision, not a cleanup.

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

## 9. Two things called Run, and one of them was a verb

**Done 2026-09-10 as a naming fix, and the entry it replaces was wrong.**

This used to say autopilot had no mode of its own and the run selector should
move out of the sidebar into a new Runs tab. Both were false. Autopilot has
lived in the Queue tab since it was written - `autopilotBar` in queue.js, and
main.js's own header says so. And `state.selectedRun` is not a Result concern:
`run.js` reads it because the rig editor inside the wizard edits that run's
poses. Moving the picker would have broken that to fix a problem that was not
structural.

The defect was one word meaning two things, adjacent. The nav item was a verb -
start one - and the sidebar label a noun - this existing one. They now read
"New run" and "Viewing", and the wizard states which pipeline it will start and
which run its rig step will edit, rather than mentioning it three steps in.


## 10. An asset type can now say what its settings start from

**Done 2026-09-10, and the entry it replaces asked for the wrong fix.**

It said `pose.source` should default to `tpose`. The survey says otherwise:
every one of the ten shipped `character_sheet` configs sets `tpose`, and all
three `animation` configs set `library`. Flipping the global default would have
been right for ten and wrong for three, and a new animation config that omitted
it would silently have got a still figure.

`ModuleSpec` already carried per-type behaviour - `props: bool = True`, whose
reader's docstring records that it exists because the same fact used to be a
string compare at a call site. It now carries `defaults` too, and
`character_sheet` declares `pose.source: tpose`. Precedence is config, then
asset type, then field.

**What is left.** Only `character_sheet` declares any, and only one setting.
Other per-type facts are still asserted globally or restated in every config;
worth a survey the next time one of them bites, not a speculative sweep now.


## 11. Counting the colours cost more than making them

**Done 2026-09-10. The entry it replaces blamed the wrong thing twice.**

It said the prepare cache's byte cap never binds because `_size` calls every
dict 64 bytes, and that `remember()` skipping large images meant nothing
checkpointed. Measured: the two prepare results are a list of 24 colours and
three scalars, so 64 bytes is an underestimate of something already negligible;
and the 6 MB refusal only ever applies to the full-resolution write path, which
runs once, while the interactive path is 384px and checkpoints fine. The LRU
was measured holding a flat 104ms across twenty edits, evicting correctly.

What was actually slow was `count_colours`, which the facts bar calls twice per
run and which had nothing to do with the cache. `np.unique(axis=0)` sorts
records through a structured-void view; packing each pixel into one uint32 makes
it a plain integer sort. Measured 49.2ms to 0.7ms at 384px, 409ms to 4.8ms at
1024px. A fully cached preview went from 104ms to 8ms.

Its sampling went with it - every seventh pixel above 400k, which under-reported
the count to save time the exact version no longer needs.


## 12. The streaking on a sheet was the display, not the pipeline

**Closed 2026-09-10 without a pipeline change; the entry was wrong.**

It said `estimate_block_size` and `find_phase` assume one lattice over the whole
image and mangle a multi-panel sheet. Measured on every real source to hand -
`sheet.jpeg`, `_source_sheet.png`, `front.png` - the estimator returns factor
1.0 and Grid passes the image through untouched, which is the correct answer and
what DIAGNOSTIC-HARNESS.md already recorded for `archer_dynamic.png`. Running a
sheet through the full stack, including the reordered stack from the report,
produces a clean keyed result with the figures intact.

What produced the streaking was `img.width`/`img.height` set as attributes on
the result image under `max-width: 100%`. The width clamps to the panel and the
height does not, so the picture squashes horizontally and stretches vertically.
08e44a2 replaced that with a stage that constrains both axes; a test now pins it.

**Still true and not a bug.** `background_to_alpha` removes 45% of that sheet -
the page between the panels - and `keep_min` only refuses a flood that would
leave under 4%. Correct here, and worth remembering as the thing to check first
if a sheet ever does lose its figures.


## 13. Two editors, one autosaver

**Done 2026-09-10.** The annotation editor kept a `dirty` flag, a Save button
and five places that set both. It now saves as you draw, through the same
module the rig editor uses.

`poseAutosaver` was debounce, coalesce, settle and status reporting, with only
the run id and the endpoint specific to poses. That is `core/autosave.js` now,
and both editors pass their own save. Measured before deciding: an annotation
save is 3.3ms and writes a 193-byte sidecar, against 77ms for a pose save that
re-renders skeletons and depth maps. Both are cheap enough; neither needed
splitting.

The tests moved with it and got better. They asserted on `rig.js` source and
would have passed while testing nothing; they now run the autosaver and check
that eight edits make at most two saves, that two never overlap, that an edit
arriving mid-save replays after it, and that a failure reports and recovers.

**Unchanged, and correct.** A pose belongs to its run and an annotation to its
image, so they still save to different places. That divergence is deliberate;
what was missing was only that nothing said which outlives the run, and a
status line on each now does.


## 14. Bone lengths were editable all along, three tabs away

**Done 2026-09-10 by making it reachable; the entry was wrong about what was
missing.**

It said the rig editor cannot change a bone's length, so fitting a long-legged
character means editing rigs.py. Everything the request described was already
built: `rigs.scale` stretches named groups and carries what hangs below,
`PROPORTION_GROUPS` names nine of them, the groups match joint names so `l_` and
`r_` move together, nine `proportions.*` fields render in the settings form, and
`resources.py:50` applies them to the rig every pose derives from.

Measured: legs x1.4 gives a leg exactly 1.400 times as long, left and right
equal to 1e-9, and the ankle carried from 0.815 to 0.939.

The defect was that you look for bone length in the rig editor and the control
lives under Settings, with nothing connecting them. The rig step now renders the
schema's own Proportions group - the same nine fields, one save path, no second
control writing the same config.

**Deliberately not built.** Per-bone handles in the canvas. Nine named groups
already cover the body, per-bone would make one forearm independently
stretchable, which is the asymmetry the request explicitly did not want, and it
would edit a pose where scale edits the rig underneath every pose.

The invariants `scale` guarantees had no tests, which is why they are pinned
now: exact ratio, symmetry, downward carry, other groups untouched, an unknown
group refused by name.


## 15. Five primitives with no caller, and each was checked one at a time

**Two adopted 2026-09-10, five examined and left.**

`Fact` and `FactGrid` matched the editor's hand-rolled facts bar exactly - same
classes, same structure, same output - so the editor calls them and no view
builds a `.factsgrid` by hand any more.

The rest were checked individually, which is what §15 asked for, and the answer
is not "adopt them":

- `Range` returns a `.control` wrapper with its own readout. All nine hand-rolled
  sliders need the bare input: the frame scrubber is driven by a timer, the
  weight painter puts its label inline, `fields.js` pairs its range with a number
  box. Converting them would fight the shape rather than share it.
- `Mono` takes its text at construction and all three candidate spans are live
  readouts mutated later. It would work and it would not remove anything.
- `Note`, `Check`, `LabelWithTip` are the same story at smaller scale.
- `BaseCard` has no caller and nothing resembling one; whether views want a card
  base is a question about a view that does not exist yet.

**What is left is a decision, not a task.** A primitive nothing needs is not
half-finished work. `Range` and `Mono` should either grow to fit what views
actually build, or be deleted; both are product calls and neither is urgent. The
dead-CSS test already stops the rules from drifting again.


## 16. The model will not paint a chroma key, and the prompt is not why

**Measured to a conclusion 2026-09-10. Two real faults fixed, and the symptom
survived both.**

The prompt said "#FF00FF", which CLIP reads as punctuation and digits. Fixed:
`name_for` says "magenta". The prompt also said "plain flat background" in the
style and "magenta chroma key background" in the backdrop clause, in that order,
so it asked for two things at once. Fixed: the phrase is gone from
`DEFAULT_STYLE` and nine configs, and `backdrop_conflict` refuses it.

The clean run of 15:49 - one background asked for, by name, with nothing
contradicting it and no background words anywhere in the style vocabulary -
produced a pale blue-grey card again. 0.0% near-magenta at tolerance 60,
corners (191, 208, 218).

**So the prompt is not the lever.** Three measurements, three fixes to what was
asked, no change in what came back. What the sprite actually sits on matches the
`hi_fidelity` exemplars, which are white, off-white and dark purple - IPAdapter
style transfer runs at 0.0-0.8 of sampling and carries the backdrop along with
the style, and it is applied per exemplar with no text to argue with.

**What is worth trying, in order.** Restrict style transfer's end_at so the
backdrop is decided after it stops; or key on what the model actually produces
rather than on a colour it was asked for, which is what `background_to_alpha`
does today by flooding from the corners and is why the pipeline works at all
despite this. §19's emphasis map is a third option and the most expensive.

**Not a bug in the keyer.** It removed 78-80% of both canonicals correctly. The
gap is only that `background.colour` claims to name what the model will paint,
and it does not.


## 17. Four editors, four meanings of reset, and no shared base

**Closed 2026-09-10 by fixing the one real gap and not building the base.**

The entry proposed a base naming `defaults()` and `saved()` for all four
editors. Surveying them first, as it said to, argues against it - they are four
different questions wearing one word:

- the rig resets one joint to the rig's rest pose
- settings drops an override so the value falls back through own, global, schema
- the annotator's Clear empties it, and empty is a real answer there, not a
  default
- the layer stack had nothing at all

Only the last is a gap. `f.default` was read when a layer was added and never
again, so a dragged slider had no way back. `BaseField` now takes an optional
`on.reset` and shows it only when the value differs from the declared default,
which makes the control both the action and the answer to "have I changed this".
Settings already had that idiom per field; this is the same one, not a new one.

None of the four gained a notion of "back to saved", and none needs one: the rig
and the annotation autosave continuously, so on-screen and saved are the same
thing, and settings models it with dirty and Save.


## 18. A held object is named once, and attached only where it can be

**Done 2026-09-10.** The reported bow was in `archer.yaml`'s subject line, and
moving it to `props` first deleted it, which is what found the real fault.

One flag governed two different things. `props: False` on `character_sheet`
means "do not attach a prop", because a socket cannot be placed on a static
multi-angle render - an argument about geometry. `frames.py` gated the prompt
words on the same flag, so a sheet could not name a bow either, and the only
remaining channel was `subject`, asserted identically in all four views
including the rear.

`wanted` is now geometry and `named` is words. A type that declines a socket
still names what the character carries; only an explicit `props_enabled: false`
or `props: {enabled: false}` silences both, because that is the config saying
no rather than the type saying it cannot.

The four configs carrying an object clause are clean: two animations moved a
sword to `props: [longsword]`, and the two archers dropped a clause that
`props: [bow, quiver]` already produced. A test refuses a weapon word appearing
in both `subject` and `props` across every shipped config.

A run now says so at the top when a config names the same object twice. That
came out of the 12:22 run, whose config.yaml was snapshotted before the archers
were cleaned: it carries the old subject clause and the props words, so its
prompt asks for the bow twice. Shipped configs are clean and a test keeps them
that way, but nothing stopped a config from doing it, and asking for a prop
twice is how a model comes to insist on one.

**Not yet measured.** Whether a sheet's rear view is better for it. The 12:22 run
does not answer it - the doubled prompt makes it the wrong control - so the next
clean run is the one to look at.


## 19. The emphasis map is authored and not yet consumed

**Half done 2026-09-10.** A weight map can be painted on a reference image and
is stored beside it as `<image>.weight.png`. Nothing reads it yet.

**Why it is shaped this way.** `ComfyUI/comfy/samplers.py` does
`mask * mask_strength * strength` on a conditioning mask that it resizes to the
latent grid, so a mask is a continuous float per cell, not a flag, and 128x128
is exactly what survives for a 1024px generation. Painting at that size loses
nothing and keeps the sidecar around 2 KB.

**What is left, and the order matters.** Wiring it into the graph means a second
conditioning through `ConditioningSetMask`, and a regional conditioning whose
weights disagree across a boundary is a known source of seams - worst on a
thin-limbed subject, which is what this pipeline makes. So the cheaper thing
first: flatten the background of the depth map that `render_depth` already
builds, which gives the model a spatial "background is far and flat" signal at
one scalar strength and needs no new nodes.

Measure before either. §16 changed the backdrop prompt from a hex code to a
colour name, and the next generated canonical says whether the backdrop obeys at
all. A weight map may be solving a problem that no longer exists.

## 20. Two runs at once, from either direction

**Done 2026-09-10.** `start_run` refused nothing: Run while a run was going
started a second subprocess, and Resume on the run already running started a
second copy of the same one, both writing the same directories and the same
`artifacts.json`. Both now raise `Conflict`.

The queue and the autopilot start runs by their own path and were not gated by
that. They wait rather than refuse, which is the difference between the two
callers: a person pressing Run has made a choice and wants to hear no, while a
queued job blocked because the machine is busy is not a broken job and the
autopilot exists to drain the queue eventually. `preflight` already separated
`problems` from `waiting`, so it is one more waiting reason and the loop already
knows to hold and retry.

Discovery lives in `shared/guard.py` as `run_in_flight`, found through the
`--run-id` a run carries on its command line. It is a fact about the machine,
not about HTTP: `_ACTIVE` is one process's memory, the autopilot is a different
process entirely, and the packaging test refused the first version for making
`orchestration` import `api`.

## 21. Conditioning is configurable on both stages now

**Done 2026-09-10.** `canonical` declared twenty-one fields and no
`ip_adapter.*`, so identity `end_at=1.0` and style `end_at=0.8` were literals,
and `canonical.from_reference` resolved to `{}` because the block canonical.py
read from was declared nowhere - a setting that existed in code and could not be
reached from a config.

Four fields now: `from_reference.weight`, `from_reference.end_at`,
`style.start_at`, `style.end_at`, each defaulting to exactly the literal it
replaced, so nothing moves until someone moves it. A test pins that.

They render beside the rig canvas through `renderGroup(..., {only})`, which
narrows a group to the paths a view is about rather than copying declarations
into it. Twenty-one Canonical fields, seven shown, one set of declarations.

**Still worth trying.** `retro_jrpg` asks the prompt for a "thick dark outline
around the whole figure" while the style exemplar votes to 0.8 of sampling,
which covers the steps that draw linework. Lowering `canonical.style.end_at`
hands those steps back to the prompt. Now testable from the UI.


## 22. Two keyers, one of which cannot read a colour

**Found 2026-09-10.** `palette.py:_key_colour` refuses anything but six hex
digits, so `12, 34, 56` and `#abc` silently disable keying and the stage floods
from the corners instead. The Definitive editor's background layer uses
`shared.colour.parse_colour` and takes both. One concept, two parsers, and the
older one is wrong.

**Also worth having.** `background.colour: auto`, sampling the canonical's own
corners. Three runs measured 0.0% near-magenta whatever the prompt asked, so the
colour the model actually produced is the only reliable key, and the corners are
where it is. Deterministic and exact; an LLM round for the same question would
return a name that needs parsing back to a number and can differ between runs.

## 23. A rig can be lengthened but not broadened

**Measured 2026-09-10 against a reference sheet.** The generated sprites read as
too slim, and the cause is not the one that looked obvious.

Height against shoulder width, which is the ratio that says "slim":

| | h/shoulder |
|---|---|
| reference sheet, front figure | **4.42** |
| humanoid rig as shipped | **6.17** |
| humanoid with the style sheet's legs 1.6, torso 1.2 | **8.37** |
| humanoid at legs 1.0, torso 1.0 | **6.17** |

So `base_pixel.yaml`'s 1.6/1.2 makes it worse, but zeroing them does not fix it:
the rig's own neutral is 6.17 before anything scales it. Every one of the nine
`PROPORTION_GROUPS` scales bone LENGTH along a chain, and none scales lateral
offset, so there is no way to make a body broader - only shorter.

Scaling the lateral axis of the shoulder and hip joints by 1.4 gives 4.41,
which is the reference. That wants a tenth group, `width` or `build`, applied to
the x component rather than to a bone length. `rigs.scale` walks parent to child
applying a factor to a distance; a width group is a different operation on the
same tree and should not be forced through the same function.

Experiment images live in `library/refs/experiment_slim/`.

## 24. A run can be started, refused, and never stopped

**Found 2026-09-10 while looking at what happens during a cooling rest.**
`cooling.rest()` sleeps the run process for `cooling.seconds` (default 180)
after every GPU task except the last. The run is alive and idle for those three
minutes, which is when someone reaches for the UI.

`POST /api/stop` exists, `api.stop(run_id)` exists in `web/js/api.js`, and
**nothing in the frontend calls it**. There is no abort control on any view.
The only way to stop a run is from the terminal.

Meanwhile "is a run going" is answered from three different places:

| Answer | Source | Survives a server restart |
|---|---|---|
| `list_runs()["running"]` | `_ACTIVE` dict in `api/runs.py` | no |
| `start_run` → `_in_flight()` | `_ACTIVE`, then `ps` for `run.py --run-id` | yes |
| `run_progress()["run"]["running"]` | `guard.run_in_flight()`, `ps` | yes |
| `POST /api/stop` | `_ACTIVE` only, else `NotFound` | no |

So after the web server reloads while a run is going, the run list shows it as
finished, Start and Resume refuse it with a 409 naming a run the page says is
not running, and Stop would raise `NotFound` even if something called it. The
UI is a dead end in exactly the window cooling makes longest.

Resume itself is correct: `_in_flight()` guards it, and the pause/resume path
through `pipeline.stop_after` is unaffected.

What it would take: one source of truth for "running" - discovery, since it is
the only one that survives a restart - and `POST /api/stop` finding the pid the
same way rather than requiring a `Popen` handle it may not have. Then one abort
control, in the same place the run's state is already shown, reusing the
existing button primitive rather than a new one.

## 25. Reasoning lives beside the code instead of in docs

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

## 26. "weight 1.00" on a reference card is not the weight

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
