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


## 15. Seven primitives still have no caller, and each needs its own look

**Half done 2026-09-10, and the earlier framing was wrong.**

This said the primitives were written and simply not adopted. Three of them did
not fit. `.ui-section` was `margin: 0 0 1.5rem` where `.group` is a surface -
background, 1px border, radius, styled heading bar - so adopting `Section` would
have removed a border from every panel. `docs/UI.md` had documented it as the
bordered container, asserted from the name rather than read from the CSS.

`Section`, `Subsection`, `Heading` and eleven unmatched `.ui-*` rules are gone.
`PanelHead` already produced exactly what five views hand-rolled and now has
five callers. Their tests went with them - they had exercised the abstraction
nothing consumed, which is confidence in code no user could reach.

**What is left.** `Fact`, `FactGrid`, `BaseCard`, `Check`, `Mono`, `Range`,
`LabelWithTip`. Each needs the check `Section` failed before it is adopted or
deleted: does its CSS match what a view actually needs. Not as a batch - a batch
is how `Section` got documented as something it was not.

A test now refuses any CSS rule that matches nothing in js, html or python, so a
third dialect cannot accumulate quietly. Verified by adding a dead rule and
watching it fail.


## 16. The backdrop was asked for in a language the encoder does not read

**Diagnosed and half fixed 2026-09-10.** The report was that the model confuses
which area is background even with a rig, and asked for a brush to paint the
answer. Measured on real output first, and the cause is upstream of any mask.

`out/runs/20260910_102428_archer/02_canonical/canonical.png` was generated from
a prompt containing "solid flat #FF00FF chroma key background". It contains
**zero** near-magenta pixels; its backdrop is a pale blue-grey, (188, 217, 225)
at every corner. CLIP was trained on captions and reads a hex code as
punctuation and digits, so the request asked for nothing at all and the keyer
was left flooding from whatever the corners happened to be - 78% removed, by
luck rather than by key.

`name_for` now maps a colour to the nearest name a caption would use and the
prompt says "solid flat magenta chroma key background". The hex is unchanged
where it matters: the keyer still matches the exact value.

**Not yet established.** Whether a named colour is enough. SDXL may still ignore
it, and the next generated frame is the measurement - count near-magenta pixels
in the canonical, as above. Only if that fails is a mask worth building, and
then as a keyer input rather than a model input: it is exact, cannot confuse
anything, and `background_to_alpha` already takes a key.

Fixed while here: `backdrop_colour(None)` raised AttributeError. The stage path
passes a merged dict so nothing hit it, but a config with no `background:` block
crashes anything calling it directly.


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
