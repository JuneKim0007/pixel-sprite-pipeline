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

**What actually has none.** `ui.suppress_overwrite_confirm` (declared, and read
by nothing), `models.lcm_lora`, and `models.clip_vision`.

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


## 14. Bone lengths are fixed, and a rig cannot be made to fit a body

**Not started.** The rig editor drags joints but cannot change a bone's length,
so a rig can be posed and not proportioned. Fitting a long-legged character
means editing `rigs.py`.

**What it would take.** Lengthening a bone moves everything below it, so it is a
downward traversal rather than a point edit. `SKELETON_TREE` in
`features/pose.js` gives parent/child and `subtree()` already walks it; both are
used by `dragJoint`. Stretching `l_hip -> l_knee` translates the knee's subtree.
Limbs come in pairs and one long leg is a mistake more often than an intention,
so pairs move together by default, with a way to break it; the `l_`/`r_` prefix
already names the pairing.

**Not as large as it sounds.** No whole-rig recalculation. A bone length is the
distance between two joints and the subtree is already computed.

## 15. The ui/ primitives are written and not adopted

**Half done 2026-09-10.** `docs/UI.md` now states the rules, the radius scale
collapsed from 13 values to 4 tokens over 83 rules, and `ColourPicker` and
`Disclosure` are shared primitives with two callers each.

**What is left.** Ten primitives are still used by no view - `Section`,
`Subsection`, `Heading`, `PanelHead`, `Fact`, `FactGrid`, `BaseCard`, `Check`,
`Mono`, `Range` - and every view still hand-rolls its heading row as
`el('div', {className: 'ovhead'}, el('h2', ...))`. Their CSS (`.ui-section`,
`.ui-h1/h2/h3`, the two body rules) still matches nothing.

Eight views, roughly 2,800 lines, and no visual regression test to catch a
mistake, so it is one view at a time and each conversion deletes the
hand-rolled classes it replaces rather than leaving both.


## 16. The backdrop is keyed after the model has already guessed

**Not started, requested 2026-09-10.** `background.colour` reaches the prompt in
`canonical` and `frames` as words - "solid flat #FF00FF chroma key background" -
and the keyer runs in `palette`, several stages later. So the model is asked for
a backdrop and only checked much later, and where the subject ends is a guess
the rig constrains but does not decide.

**The request.** Paint the area that is backdrop, with a brush, and have that be
authoritative rather than advisory.

**What has to be established first.** Whether the mask should reach the model or
only the keyer. These are different mechanisms and the second is nearly free:

- As a keyer input, a painted mask is exact and cannot confuse anything. It is
  the same shape `annotate.py` already stores per image, and `background_to_alpha`
  already accepts a `key=` colour, so the layer to change is small.
- As a model input it is inpainting or a ControlNet mask, which changes what
  `canonical` submits to ComfyUI, and a mask that disagrees with the prompt is a
  real way to make output worse rather than better.

Websearch and measurement both required. Do not implement from the request
alone - the ordering that looks wrong may be deliberate, and DECISIONS.md should
be read for the backdrop wording before anything moves.

## 17. Three editors keep only the state that is on screen

**Half done 2026-09-10.** The rig's reference pose now comes from the rig
(`rigDef.neutral`) rather than from frame 0, so Reset returns a joint to the
rest position and `dragJoint` snaps bone lengths against the right reference.
Measured: neutral and the T-pose differ on 4 of 18 joints, so the two were
never interchangeable.

**What is left, and it is the wider shape.** Every editable set here has three
states - what the rig or schema defines, what was last saved, and what is on
screen - and only the third is modelled. The rig editor now has the first;
annotations, the layer stack and the settings form still have none, so each
answers "reset" differently or not at all.

A base naming `defaults()` and `saved()`, with reset meaning "back to saved, or
to defaults when never saved", would serve all four. `ui/BaseField` and
`ui/BaseCard` are the precedent for putting that in one place, and `BaseCard` is
still unused, so the shape is available rather than absent.

Do not generalise from the one case that is fixed. Look at whether the other
three want the same base first.


## 18. The subject line asks for the weapon it is being blamed for

**Diagnosed 2026-09-10, not started.** The reported symptom was that an
annotation does not stop a weapon appearing. The cause is upstream of the
annotation. `library/configs/archer.yaml:12` ends:

    ... chunky black boots, a quiver of arrows, holding a curved black bow

The model is drawing what it was asked for. No amount of ControlNet weight
fixes a prompt, and raising it to suppress a prop stiffens every pose - a real
cost paid for a symptom it does not cause.

**What the field is for.** `subject` is identity: who the character is, and what
they wear, held constant across every view of a sheet. A held object is not
identity - it is per-frame, which is why `prompt_for` takes `held` as its own
argument and why `props:` exists in the config. A bow in `subject` is asserted
in all four views including the rear, where a character sheet wants the body.

**What it would take.** Sweep the shipped configs for object clauses that belong
in `props` or `held` rather than `subject`, and say so where the field is
described - `schema.py`'s help for `subject` does not currently distinguish
identity from what a character happens to be carrying. A validator that refuses
"holding ..." in `subject` is the stronger version and should wait until the
sweep shows how common it is.

Supersedes the earlier reading of this entry, which assumed the annotation was
too weak. It was not. Related to §16 only in that both wanted a way to say "not
this"; this one does not need one.
