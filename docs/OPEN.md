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
`Context` and reads `settings`, `stage_dir`, `run_id`, `root`, `outdir` off it.
One `apply(inputs, cfg, prep)` means those five arrive as inputs instead, which
rewrites seven stage bodies.

**Why not yet.** It cannot be verified here. A real pipeline run needs ComfyUI
and a GPU, so a rewrite of the stage bodies would be checked only by tests that
assert structure, never by a render. The return contract above was worth doing
precisely because it *is* fully coverable.

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

So it is enforced instead of restructured. Two checks in
`tests/unit/test_contracts.py`: every `settings("path")` found by walking the
AST has a field declared at or under it, and every field prefix names either a
stage, a block something reads through `settings()`, or one of three blocks
read another way — `cooling` through `cooling.rest`, `detect` inside
`refs/detect.py`, `pipeline` from `run.py` before a `Context` exists.

**Still true, and not covered:** a field whose block nothing reads at all. The
reverse check is all false positives, because several blocks are read through
raw config, the queue, or `ctl.sh` rather than `settings()`.

## 4. `DEFAULT_GLOBAL` still holds five settings with no control

`paths.input_dir`, `paths.output_dir`, `paths.download_dir`,
`ui.suppress_gate_confirm`, `ui.suppress_overwrite_confirm`, plus
`models.lcm_lora` and `models.clip_vision`. They have no `ConfigField`, so the
settings form has never offered them and still does not.

**Why not.** Inventing controls for them is a product decision, not a cleanup.

## 5. The stylelog writes half of what it reads

`views/styles/styles.js` renders `train` and `tune` events;
`looks/stylelog.py` has `tune_event`, `train_event` and `archive_training` and
nothing calls them. A half-built feature, not dead code — deleting the writers
would leave live readers for a format nothing can produce.

**Why not.** Finishing or removing it is a product call.

## 6. Ten write routes are declared but never driven

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

## 7. Ten raw `ctx.config` reads remain

Down from 28. Each remaining one is either not a schema field (`props`,
`paths.*`) or wants a different fallback than the schema would give —
`subject` and `style`, where `canonical` and `frames` fall back to a prompt
string and the LLM palette chooser deliberately passes an empty one.

Reasoning: `docs/CONFIGURING.md`, "The four settings with no default".
