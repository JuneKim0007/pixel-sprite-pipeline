# What is still open

Work that is known, named and deliberately not done. Each entry says what the
state actually is, what it would take, and why it has not been taken — so the
next person does not rediscover it, and does not start it thinking it is
smaller than it is.

Anything finished leaves this file and lands in the commit that closed it.
Measurements here are dated; re-measure before trusting one.

---

## 1. `Node` execution, not just declaration

`Stage` and `LayerSpec` speak one vocabulary — `needs`/`gives` over
`shared/plan.py` — but still run through two engines with two signatures:
`run(ctx, prep)` and `apply(img, cfg, facts, prep)`.

**What it would take.** One `apply(inputs, cfg, prep)` rewrites all five
builtin layers, both budget guards, the cache keying (it fingerprints an
ndarray directly), the deferral machinery, `magnify`/`growth` and `admit`; on
the stage side every use of `stage_config`, `stage_dir`, `run_id`, `root` and
`outdir` needs another route.

**Why not.** `NODES.md` §4's own justification is spent — §3a argued the split
was needed to make stages cacheable, and step 3 delivered that on 2026-08-13
without any `Node`. What is left is "one shape", with no measurement behind it.

Design: `docs/NODES.md` §4.

## 2. `Context` is still reachable from inside a stage

A stage declares `needs` and reads `ctx.need("rig")`, so the dependency is
checked before the run. But because it holds `ctx`, nothing stops it reading a
resource it never declared. Only passing resources as arguments makes that
impossible rather than merely checkable — which is item 1.

## 3. `schema.FIELDS` is a parallel list, not a projection of the nodes

The last line of `NODES.md` §6.1. Fields and stages are declared separately and
joined by a dotted-path prefix.

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
