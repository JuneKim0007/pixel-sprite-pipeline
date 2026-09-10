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

## 9. The Run tab does two unrelated jobs under one name

**Not started.** `renderRun` configures and starts something new. The sidebar
`RUN` selector beside it picks an existing run to inspect and resume. Both are
called Run, and the selector sits in global chrome, so a freshly-chosen config
shows a run id next to it and reads as though that run is about to be re-run.
Autopilot has an API (`api/jobs.py`) and no mode of its own, which puts a third
job in the same place.

**What it would take.** Split the nav: New, and Runs. Move the run selector out
of the sidebar. `STEPS`, `reviewStep`, `confirmStep`, `gateBanner` and the
resume path in `result.js` already exist; this is re-routing, not rewriting.

**Why not yet.** Every other UI entry sits inside the surface it moves, so it is
worth doing first or last, never in the middle. §16 should precede it.

## 10. The pose default is `library/idle`, not a rest pose

**Not started.** `pose.source` defaults to `'library'` with `pose.name: 'idle'`,
so a new humanoid rig starts from a library animation rather than the neutral
spread `rigs.tpose` produces. One default change, to `'tpose'`.

**Not the same question as the angle.** `A_POSE_DEGREES = 40.0` was measured: 88
degrees reads to the model as holding a weapon, and arms-down puts joint pairs
within 4% of the canvas so the silhouette has no gap for ControlNet. The angle
should become a setting before anyone changes it.

## 11. `Cache._size` calls every dict 64 bytes

**Measured 2026-09-08, unfixed.** `cache.py:26` returns 64 for anything that is
not an ndarray, list or tuple. Prepare results are dicts, so the prepare cache's
8 MB cap has never bound; only its 64-entry cap has. DIAGNOSTIC-HARNESS.md
records this as a reading fault in the harness output. It is the cache.

Separately `remember()` refuses any image over `SNAPSHOTS.max_bytes // 4` (6 MB).
At the 384 px preview nothing reaches that; at full resolution every checkpoint
is skipped in silence and each edit recomputes the stack from the first layer.

Both point at "the editor gets slow after a few layers", which is the reported
symptom and has not been reproduced under measurement.

## 12. Grid measures one lattice for a picture that has several

**Not started.** On a multi-panel character sheet the result is mirrored
vertical streaking. `estimate_block_size` and `find_phase` assume one lattice
over the whole image; a sheet is several drawings with their own. Either refuse
a source whose measured block size has no agreement across regions, or measure
per region. Refusing is smaller and honest.

## 13. The annotation still saves by hand, and nothing says which save is durable

**Half done.** The rig side closed on 2026-09-10: pose edits autosave and the
dialog is gone. `annotate.js` still keeps its own `dirty` and `Save annotation`.

Poses belong to a run; annotations are a `<image>.rig.json` sidecar reused by
every run that uses that image. Moving annotations into `out/runs/<id>/` would
copy them per run and break that reuse, so the split stays. What is missing is
anything on screen saying which of the two outlives the run.

## 14. The result view is a flat grid with no structure and no inputs

**Not started.** Each stage's PNGs render as one undifferentiated grid. Two
things are missing and they are the same fix: the stages do not collapse, so a
run with six of them is a wall; and nothing shows an input as the machine
consumed it - a skeleton over the reference it was fitted to, a depth map
against its frame - which is what makes a bad pose obvious.

The history strip has the same gap. A run is identified by a timestamped id, so
telling two apart means opening both; the reference image a run was built from
would identify it at a glance, and `run_audit` already reads
`references.identity` out of the run's own config.

**Unblocked 2026-09-10.** Every run had failed on a moved reference path until
then, so there was nothing to lay out. `20260910_102525_char_3` has four
skeletons and four depth maps.

## 15. Bone lengths are fixed, and a rig cannot be made to fit a body

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

## 16. The UI has no written layout rules, so every view invented its own

**Not started.** Nothing says when a thing is a box, when boxes nest, what a
panel's ratio or minimum is, or where an action belongs. Each view answered
separately and the answers disagree: `.card`, `.pane`, `.stackpanel`,
`.stackform`, `.group`, `.histbox` and `.auditbox` are all a bordered container
with a heading, with different padding, radius and border rules.

The editor's four cards became one shell with dividers on 2026-09-10, which
fixed one view and widened the gap with the rest.

**What it would take.** Probe the elements and name the responsibilities that
are actually present, write the rules down, then reduce the classes to them. The
document is the deliverable; the refactor follows it.

**Why before more UI work.** §9 and §14 both add layout. Adding it without rules
means two more dialects.
