# The vertical half: nodes, pipes and the contracts between them

`REFACTOR.md` did the horizontal work — grouping modules and giving them a
`shared/` kernel that depends on nothing. This is the other half it names but
does not design: `layer_connection/`, and what a node actually is.

Measured 2026-08-13, after steps 1–5 of REFACTOR.md §6. **Re-measured
2026-08-26**; the §1 table and §6 below carry the current state.

---

## 1. Where the plan stands

| REFACTOR.md §6 | State | Evidence |
|---|---|---|
| 1. Split `stage.py` | **Partial** | `opt` → `shared/config.py` ✓, registry → `shared/registry.py` ✓. **`Context` never moved** — still ~130 lines in `stage.py`, four of them deferred imports of other groups. |
| 2. `shared/errors.py` + one handler | **Done 2026-08-14** | Handler is one block ✓, and the builtin count on user-reachable paths went 67 → **0**, held there by `tools/check_failures.py` in `make lint`. |
| 3. `shared/registry.py` | **Done** | 7 registries on one generic, incl. the route table. |
| 4. Move the modules | **Done** | Seven groups; `shared/` leaf-ness is test-enforced. |
| 5. Split `server.py` | **Done** | 1,307 → 119 lines, routes are data. |
| 6. Contracts | **Not started** | And the spec shapes went from two to three. |
| — | **Added 2026-08-26** | The three group cycles are gone; `tests/flows/test_packaging.py` refuses a new one. |

The horizontal work is essentially finished. Everything below is the part that
was deferred, and the deferral is now the largest source of coupling left.

---

## 2. The finding: ~~three~~ **two** node systems, one shape

> **Corrected 2026-08-26.** `Job` is not a node system. Measured: it is a
> `path` plus a `data` dict with properties for `module`, `config`, `attempts`
> and `state`. It has no fields, no `prepare`/`apply`, and no artifacts — its
> `needs` (`queue.py:231`) is a list of **file paths that must exist**, not
> artifacts a sibling produces. It is a scheduler entry. The only property it
> shares with `Stage` and `LayerSpec` is "a declared unit of work composed in
> an order", which is also true of a cron entry. The `Job` column below is kept
> because the comparison is still informative; the conclusion drawn from it was
> not.

The same argument as REFACTOR.md §2 ("six registries, three implementations"),
one level up. Three things in this codebase are *a declared unit of work
composed in an order*, and all three were written separately:

| | `Stage` | `LayerSpec` | `Job` |
|---|---|---|---|
| declares | `name, requires, produces, optional, resource, DEFAULTS` | `key, label, summary, fields, order, repeatable` | `config, needs, priority` |
| work | `run(ctx) -> dict` | `prepare(img,cfg)` + `apply(img,cfg,facts,prep)` | external process |
| composed by | `runner.build/validate/plan/run` | `run.apply_stack` + `check_order` | `queue` + `preflight` |
| dependencies | artifact names, **hard-validated** | position only, **advisory warning** | job ids |
| caching | **none** | prefix-keyed snapshots + prepare cache | none |
| parallelism | batched by `Resource` | serial, semaphore | serial, cooling |
| config spec | `DEFAULTS` + `schema.FIELDS` | `Field` (label+help mandatory) | none |

Each cell that differs is a decision made three times. The three that cost
something are below.

---

## 3. The three asymmetries that cost something

### 3a. ~~Stages have no `prepare`/`apply` split, so the pipeline cannot cache~~

> **Closed 2026-08-13** by step 3 below, and the measurement it argues from is
> the measurement that closed it: `find_phase` went 5 calls to 1 on a four-view
> sheet, and `palette.phase` was deleted. Left here because §4 leans on it, and
> a reader taking it at face value would rebuild something that exists.

`LayerSpec` separates the *orderless* half (measure a block size, cluster a
palette, find a lattice phase — a pure function of the arriving image and that
layer's settings) from the *ordered* half. That split is why the editor can
skip work when you move an unrelated slider.

`Stage` has no such split, so nothing in the pipeline is cacheable. The cost is
concrete and measured:

- `palette.phase` defaults to `per_frame`, so `find_phase` runs **once per
  frame**. At 5.2 s/MP and factor 16 that is ~0.5 s per 0.1 MP frame, ×N frames.
- `phase: locked` exists as a config option to work around exactly this — it
  computes the phase once on the canonical and reuses it. **That is a manual
  cache with a config key as its invalidation policy.** A `prepare` result
  keyed on (image fingerprint, settings) would make it automatic and correct,
  and would delete the option.

The editor already has the machinery (`definitive/cache.py`: content-hash
memoization, prefix snapshots, LRU with byte budgets). The pipeline has none of
it, for no reason other than the two node systems being written apart.

### 3b. Layer ordering is advisory where stage ordering is validated

`runner.validate` refuses a stage order whose dependencies cannot be satisfied,
naming the producer that runs too late. `check_order` returns *warnings* — put
`palette` before `grid` and you get a sentence, not a refusal, and the run
proceeds to produce a palette measured from pixels that no longer exist.

The asymmetry is not principled. Layers have real data dependencies (`palette`
needs the reduced image; `background` needs the un-keyed one) — they are simply
not declared, so they cannot be checked. Giving `LayerSpec` `needs`/`gives`
turns three hand-written warnings into the same validator stages already use.

Once `palette.stack` is orderable from a style sheet (the pixelize plan), an
advisory warning becomes the only thing between a user and silently wrong
colour — which is the failure `shared/registry.py` was built to end.

### 3c. Three declaration shapes, and I added the third

| shape | count | carries |
|---|---|---|
| `schema.FIELDS` | 126 | path, label, type, help, min/max, group, `when` |
| `definitive.Field` | 20 | key, label, kind, help, min/max, default, `when` |
| `Stage.DEFAULTS` | 7 | key → default only |

REFACTOR.md §5c is explicit: `Field` is the closest to right, because a field
cannot exist without a label and an explanation and a test enforces it — "the
others should converge on it rather than the reverse."

`DEFAULTS` (added this session) moved *away* from that. It solved the real
problem — defaults were declared at the `opt()` call site and the form could
show none of them — but it solved it by inventing a third shape rather than
giving stages the `Field` they should have had. It is an improvement that
should not survive contact with this design.

---

## 4. The design

One node contract, one plan builder, and `Context` inverted.

```python
# flow/node.py  (REFACTOR.md calls this layer_connection/)

@dataclass(frozen=True)
class Node:
    key: str
    label: str
    summary: str
    fields: tuple[Field, ...]          # the ONE spec shape; default lives here
    needs: frozenset[str]              # artifact names, not positions
    gives: frozenset[str]
    optional: frozenset[str] = frozenset()
    resource: str = Resource.CPU

    # Pure. A function of the inputs it reads and its own settings, and of
    # nothing else — which is the claim that makes the result cacheable.
    def prepare(self, inputs: Mapping, cfg: Mapping) -> dict: return {}

    # Ordered, cheap. Nothing here measures, searches or clusters.
    def apply(self, inputs: Mapping, cfg: Mapping, prep: Mapping) -> dict: ...
```

`Stage` becomes a `Node` whose inputs are artifacts; `LayerSpec` becomes a
`Node` whose inputs are `{"image": ndarray}`. The runner and `apply_stack`
become one `Plan`:

```python
plan = Plan(nodes, seeded=set(artifacts))   # raises on an unsatisfiable order
for batch in plan.batches():                # groups independent CPU work
    for node in batch:
        prep = cache.get(key(node, inputs, cfg), lambda: node.prepare(inputs, cfg))
        inputs |= node.apply(inputs, cfg, prep)
```

> **What was built, 2026-08-26, and what was not.** The *declaration* is
> unified and the *execution* is not, on purpose.
>
> `Stage` and `LayerSpec` now speak one vocabulary: `needs` and `gives`, where a
> need is a name that must be satisfied before the node runs and **where it
> comes from is the plan's business, not the node's**. For a stage that is an
> earlier stage's `gives`, a seeded artifact, or the run's resource table;
> `Stage.requires`/`produces` are gone. `shared/plan.py` holds the one
> dependency walk both engines make, parametrised by the single thing that
> differed — whether a name nothing gives at all is a hole (`strict`, stages)
> or a non-event (`ordering`, layers). It replaces the same walk written twice.
>
> Merging `run(ctx, prep)` and `apply(img, cfg, facts, prep)` into one
> `apply(inputs, cfg, prep)` was **rejected**, and not for size. It rewrites all
> five builtin layers, both budget guards, the cache keying (which fingerprints
> an ndarray directly), the deferral machinery, `magnify`/`growth` and `admit`;
> and on the stage side every use of `stage_config`, `stage_dir`, `run_id`,
> `root` and `outdir` needs another route. Against that, §4's own measured
> justification is spent: §3a argued the split was needed to make stages
> cacheable, and step 3 delivered that saving in 2026-08-13 without any `Node`.
> What is left of the merge is "one shape", with no measurement behind it.
> `AGENTS.md` says measure before asserting something is better — this says that
> instead.

**`Context` stops being a service locator.** Today a stage asks it for a rig
and it resolves one — the reason for the five deferred imports, and the reason
`props` looked like it depended on an LLM. Inverted, a node *declares* `needs =
{"rig"}` and the connection layer supplies it. That is the move REFACTOR.md
§4 already argues for, and `requires`/`produces` is a working prototype of it:
the machinery exists, it is just tangled with the locator.

---

## 5. Patterns, per concern

Named only where a name earns its keep.

| Concern | Pattern | Why this one | Already half-there |
|---|---|---|---|
| Where nodes come from | **Strategy** (`Decorated` / `Scanned`) | Two population methods, one behaviour for missing/duplicate/broken | `shared/registry.py` ✓ |
| `prepare` then `apply` | **Template Method** | The base fixes the sequence and the cache boundary; a node cannot opt out of being cacheable | `definitive/run.py` ✓ |
| Supplying rig/refs/paths | **Dependency Injection**, replacing **Service Locator** | Declared needs are checkable before running; a locator's are discoverable only by running | `requires`/`produces` ✓ |
| Order legality | Topological validation — *not* a GoF pattern | It is a dataflow DAG; calling it Chain of Responsibility would obscure that | `runner.validate` ✓ |
| Cooling, retry, timing | **Decorator** around `apply` | Cross-cutting, must not be re-implemented per node | `cooling` is inline today ✗ |
| Reusing work | Content-addressed **memoization** | Keyed on input fingerprint + cfg, so it is correct under reordering | `definitive/cache.py` ✓ |
| Response shapes | Declarative contract, validated at import | Same property `make check` gives configs | ✗ (REFACTOR.md §6.6) |

The honest note: five of seven already exist, in one of the two node systems.
This is mostly a consolidation, not a construction.

---

## 6. Order of work

Each step is independently useful and independently verifiable.

1. **`Field` becomes the one spec shape.** *Half done 2026-08-14:* both
   `schema.FIELDS` (137) and `definitive`'s 20 are `Field` subclasses, and the
   "every field carries an explanation" test now covers all 157 — the twenty
   that answered it with the word `TODO` were written out 2026-08-26 and
   `Field` refuses a placeholder. A field can now also carry its own default:
   `_render` used to `del base["default"]` unconditionally, so the only source
   was `Stage.DEFAULTS` — and `_declared_default` stops at one dot, which left
   **all 34 nested fields with no default at all**. 29 are now declared where
   their bounds are, each verified against the `opt()` call site that reads it
   and pinned there by a test. The other 5 have no static answer: two are
   conditional (`0.55 if strong else 0.30`), one is computed from the
   references, and two mean "follow the rig" when blank.

   **Finished 2026-08-26.** `stage_config` merges branch by branch, so a nested
   block keeps the siblings a config leaves alone — the shallow merge was the
   only thing stopping nested fields from carrying defaults. `DEFAULTS` went
   from 52 keys across seven stages to **three across three**, and the three
   left are what a field cannot express: `canonical.from_reference`,
   `pose.views`, `softbody.nodes`. `frames` declares none.

   The third copy went with it. Twenty-nine `opt(block, "key", <literal>)`
   fallbacks were unreachable once the block arrived filled, so they are
   subscripts now and the literal exists once. Verified by diffing
   `stage_config` for every stage across all 15 configs before and after: 290
   keys appeared, **0 values changed, 0 keys vanished**.

   Config blocks that are not stages resolve the same way: `stage_config`
   generalised into `settings(path)`, which takes any dotted prefix, so
   `references.match` gets its declared defaults exactly as `frames.controlnet`
   does. `compute.vram_mode` is the one field left out on purpose — `ctl.sh`
   reads it out of the YAML directly.

   *Left:* making `schema.FIELDS` a projection of the nodes rather than a
   parallel list.
2. **`LayerSpec` gains `needs`/`gives`.** **Done 2026-08-26.** `grid` gives
   `reduced`; `palette` and `background` need it. `validate_order` refuses an
   order that satisfies a need too late, with the same shape `runner.validate`
   has always used for stages — the layer named, the producer named, and a hint
   saying where to move it. It runs before `admit`, so a wrong order costs
   nothing.

   Only **two** of the four rules were dependencies. "Scale is not last" is a
   cost rule and "grid appears twice" is a uniqueness rule; forcing them
   through `needs`/`gives` would have been the wrong abstraction, so
   `check_order` survives as the advisory half and says so in its docstring.

   A need nothing gives is satisfied vacuously: a stack with no `grid` has no
   lattice for `palette` to contradict, so it is consistent even though it is
   unreduced. That is why this is an ordering constraint and not `requires`.
3. ~~**`Node.prepare` on stages.**~~ **Done 2026-08-13.** `Stage.prepare(ctx)`
   runs once and the runner hands its result to `run(ctx, prep)`. The palette
   stage's colours and lattice are both `prepare` results; `palette.phase` is
   deleted. Measured: `find_phase` 5 calls → 1 on a 4-view sheet, saving 5.4s
   there and 16.2s on a 12-frame animation. The premise was measured too —
   6/6 frames of one run recover an identical phase, so `per_frame` was paying
   N times for one answer.
4. **`Context` inverted into `flow/`.** Nodes declare `needs={"rig"}`; the
   connection layer resolves. **Done 2026-08-26**, short of the full `Node`
   rewrite. A stage declares `needs = frozenset({"rig"})` and reads
   `ctx.need("rig")`; `generation/resources.py` is the only place a resource is
   resolved, and `runner.validate` refuses a need no resolver can answer before
   any GPU work. `stage.py` has **no cross-group deferred imports** left — the
   two remaining are `.schema` and `.resources`, both same-group, and `.schema`
   is a real intra-group cycle that step 1 has to break. `Context` is 53 lines,
   not under 40: `stage_config`, `stage_dir`, `require` and the config clamp
   all stay, and none of them is a locator.

   The transient/persisted split is now structural instead of typographic.
   `artifacts` is what stages pass each other and what a resume reads back;
   `resources` is what the run supplies against `needs` and is never persisted.
   They were one dict distinguished by a leading underscore, enforced in a
   single line of `orchestration/artifacts.py` and documented nowhere.

   **Left for the `Node` rewrite:** because a stage still receives `ctx` and
   reads from it, `ctx.need("rig")` cannot verify that *this* stage declared
   it. Only passing resources as arguments makes undeclared use impossible
   rather than merely checkable.
5. **Middleware.** Cooling and retry become decorators around `apply`.
6. **Contracts**, last, once routes and nodes both have shapes to attach.

Steps 1–3 are worth doing whether or not 4–6 happen. Step 4 is the one that
pays down the coupling REFACTOR.md opened with, and it is much easier after 1–3
because by then the node contract is the only thing `Context` is feeding.

---

## 7. The cycles, and why each existed (removed 2026-08-26)

Measured over the AST rather than read: 165 cross-group import statements, and
exactly three cycles, each smaller than §1 implies.

| cycle | statements | cause | fix |
|---|---|---|---|
| `generation ↔ orchestration` | 3 | `cooling.py` imports `time` and nothing else. It is a leaf that was filed into a group, and §5 already calls it cross-cutting. | moved to `shared/` |
| `definitive ↔ looks` | 3 | `estimate_block_size` is lattice measurement filed under training, and the palette layer reached the asset registry through `facts["root"]`. | measurement moved beside `find_phase`; `apply_stack` takes a `palettes=` resolver, so the dependency is visible at the composition root instead of ambient |
| `geometry ↔ refs` | 4 | `annotate.gather(root, cfg)` loaded its own reference library, while both callers held a `Context` already caching a better-configured one. | `gather(library)` |

The palette move is Dependency Injection replacing Service Locator, as §5
prescribes. A module-global provider slot was considered and rejected: it
satisfies the import graph while keeping the property that makes a locator bad
— you cannot tell from a layer's declaration what it will reach for.

The `gather` change fixes a bug on the way past. The library it built lacked
`_name` and `_runs_dir`, so `from_pattern` and `from_run` contributed nothing
to it; annotations on pattern-derived or inherited references were invisible to
proportion measurement. Passing the run's own library makes them visible.

`tests/flows/test_packaging.py::test_no_group_imports_form_a_cycle` walks the
graph and names both edges of any cycle it finds. Verified red by adding
`from ..looks import training` to `definitive/cache.py`.
