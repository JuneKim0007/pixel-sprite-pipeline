# Refactor backlog

Surveyed 2026-08-28 · re-surveyed 2026-09-01 after the campaign merged · scope
`pipeline/ tools/ autopilot.py run.py server.py` · 72 files
Baseline: 635 pytest + 1 xfailed · 120 frontend · `make check` clean · 12,794 lines
· 86 comment lines · master dd62c9f

Long methods 16 -> 4. Nothing at nesting depth 5. Zero dead code, zero unused
imports, zero cross-file duplicates, zero truncated comments — all measured on
the merged tree, not the branch.

Ids are permanent. `Done`, `Dropped` and `Refused` keep theirs; the next id is
one past the highest ever issued.

---

## Open

**Nothing here is blocked.** Re-swept 2026-09-02: no dead code, no unused
imports, no cross-file duplication in source. The two remaining source
repetitions are irreducible — the `sys.path` bootstrap in the two entry points
cannot be factored into a module that needs that path to be importable, and
argparse declarations are what the library asks for. Every entry point the sweep found untested now has
tests, each mutation-checked. Long methods are down from 16 to 5 and nothing in
scope sits at nesting depth 5. What is left is one deferred item and one where
the remaining shape was judged not worth changing.


### R34 · Long method · autopilot.py:135 · work
status   open · accepted regression
evidence 57 statements at depth 4, up from 55 at depth 3. The drain fix in
         ff31230 added a branch inside the empty-queue arm: re-check the held
         jobs, continue if any was released, exit otherwise.
remedy   none scheduled. The depth is the cost of R32 being fixed correctly
         rather than by exiting straight away, which would have stranded a job
         unblocked mid-run. Reopen only if `work` grows past 60 statements.
blocked  none
first seen 2026-09-01

### R11 · Shotgun surgery · 7 stage files + generation/stage.py
status   deferred · gated ASK 2026-09-02, answered: correct the reason, leave
         the code
evidence The stage↔settings contract moved 7 times, each editing 4-7 of the 7
         stage files: 1462901, db13bb5, fce2794, 451ba86, 12a8b85, 97ad0b4,
         65ddf8c. Threshold 3/3; measured 7/7. The trend is downward — 7 stage
         files per change, then 5.
remedy   docs/NODES.md §4's `apply(inputs, cfg, prep)`, per docs/OPEN.md §1.
blocked  NOT by coverage any more. All seven stage bodies now execute under
         test — c1d3908 closed the last four — and OPEN.md §1's "needs ComfyUI
         and a GPU" was wrong for five of seven stages. That reason is
         corrected in the doc rather than left to mislead the next reader.

         What blocks it now is a design question, not a test gap. Two of the
         nine Context members a stage reads are behaviour rather than values:
         `ctx.need` is lazy and memoised because `rig: auto` costs an LLM call,
         and `ctx.stage_dir` creates the directory and assigns its number when
         called. Passing either in as an input moves when that work happens, so
         the rewrite is a design change and not a refactor. Radius measured:
         105 access sites, 10 source files, 9 test files.

         Asked and answered 2026-09-02 — hold, correct the reason, do not
         rewrite. Do not re-ask without new evidence.
first seen 2026-08-28 · re-gated 2026-09-02

---

## Done

### R1 · Duplicate code · stages/canonical.py + stages/frames.py · generation preamble
closed 2026-08-28 by fd2d8d1 — comfy.connect, vocabulary.backdrop_colour and
vocabulary.prompt_for. The liveness sentence had been written out identically in
both files; a test now asserts the two stages say the same thing.

### R2 · Duplicate work · stages/canonical.py · run
closed 2026-08-28 by 41e1bc2 — the conditioning was computed before the loop and
again by `_prepare(view)` inside it, which overwrote all five nonlocals before
anything read them. Every run uploaded one PNG to ComfyUI and discarded the
name. 35 lines out, one upload per run saved.

### R3 · Switch statements · definitive/pixelize.py · apply_fixed_palette
closed 2026-08-28 by 63541c0 — the four colour-space projections were a dispatch
table in one function and an if/elif chain 90 lines below it. Both call
`project()` now.

### R4 · Long method · pipeline/stages/frames.py · run
closed 2026-08-29 by 3c74587 — 107 statements to 78. `_frame_prompt` and
`_anchor_weight` extracted; the frame's yaw had also been computed twice under
two names.

### R5 · Long method · pipeline/stages/canonical.py · run
closed 2026-08-29 by 41e1bc2 and 3c74587 — 120 statements to 76. The duplicated
conditioning went first; then `build`, a 40-statement closure over eight
run-scoped names, became `_AnchorGraph` with identity, style and control as
three methods and the upload cache as a field.

### R6 · Long method · run.py · main
closed 2026-09-01 by 2da1236 and the refactor behind it — 71 statements to 46.
`_parse`, `_resume`, `_fresh`. Twenty-one tests first; there were none.
The entry's claim that the outdir expression was written twice was wrong: the
two read different sources, and necessarily — a resume cannot use the effective
config because the file it merges from is inside the directory being located.

### R7 · Long method · autopilot.py · work
closed 2026-09-01 by b403dbd and the refactor behind it — 68 statements to 55.
`_fail`, `_tripped`, `_await_services`; `idle_since` was a timestamp nothing
read. Seventeen tests first. The breaker was written at both failure arms and
only one was covered, so mutating the other failed nothing until a test for it
existed.

### R9 · Long method · the six that were over threshold
closed 2026-09-01. depthmap.render_depth 37 (was 44) by b80cf1d ·
runner.run 24 (was 47) by aa35b2d · pose.run 28 (was 43) by 4de0eac ·
pixelize.reduce_blocks 4 (was 43) and background_to_alpha 18 (was 42) by
a871844 · autorig.fit_humanoid 25 (was 87) by the _Figure method object.
reduce_blocks held five algorithms in one if-chain over five recomputed locals;
they are `_Blocks` and a dispatch dict, matching the idiom already in the file.

### R10 · Nesting only · 3 methods at depth 5
closed 2026-09-01. queue_act 13/depth 3 (was 17/5), rigs.scale 22/3 (was 33/5),
list_runs 14/2 (was 29/5). queue_act was the blocked one — its three refusals
share one function and the order decides which a caller sees — so 9767066 pinned
that order with eight tests before anything moved. Nothing in scope sits at
depth 5 now.

### R8 · Long method · pipeline/definitive/run.py · apply_stack
closed 2026-09-01 — 59 statements at depth 4 to 6 at depth 1, as `_StackRun`.
This entry previously said the rest was not worth doing, on two grounds that
were both true and neither sufficient. Extraction cannot reach past a
`continue` — but the four continues were not needed: `cache.remember` fires in
exactly the cases that are neither already-cached nor unknown-layer, so the
checkpoint belongs at the end of one branch. And a helper preserving the exact
order of the run would need seven parameters — which is what a method object
dissolves, because the seven are the run's own state.

### R33 · Large class · stages/canonical.py · _AnchorGraph
closed 2026-09-02 by 76455a6, and not by the fix this entry expected. It had two
field clusters because `_loaded` held an upload cache nothing else touched, and
extracting that cache would have made a lazy class. The cache was not the
class's business at all: three of them existed across two stages, all answering
"has this file been sent yet", which the client knows. Memoising
`Client.upload_image` removed all three, `comfy.load_image` took the node
builder, and `_loaded` had nothing left to be. One cluster over five methods.

### R35 · Duplicate code · reading a config · 5 modules
closed 2026-09-02 by 961f81c — `yaml.safe_load(p.read_text()) or {}` at nine
sites is `settings.read_yaml`; the override loop at three is
`schema.apply_overrides`; `styles.layer` followed by `settings.effective` at
four is `styles.effective`. Each placed where no group gained a dependency:
shared still has no outgoing edge and the graph has no cycle.

### R36 · Duplicate code · three Context fixtures
closed 2026-09-02 by ccdf8b6 — gpu_ctx, cpu_ctx and pose_ctx were one fixture
with cosmetic differences. `entries` was two functions that looked
interchangeable and were not, since the GPU stages read only the yaw; the
difference is now the argument `posed`.

### R37 · Long parameter list · autorig._confidence, palette._extract_from_subject
closed 2026-09-02 by d395440 — seven arguments of which three were already
fields on the object being computed for, and eight of which five were unpacked
from one config block. Both were left by earlier refactors in this campaign.

### R12 · Long method · orchestration/queue.py · preflight
closed 2026-08-28 by 63541c0 — 60 statements at depth 5 → 14 at depth 2, eight
check functions, each keeping the local import it needs.

### R13 · Duplicate code · geometry/props.py + geometry/softbody.py · from_config
closed 2026-08-28 by 7963465 — 13 identical statements → shared.contracts.from_entry,
parameterised by noun and tuple keys.

### R14 · Inappropriate intimacy · run.py · seed_stage_numbering
closed 2026-08-28 by 7963465 — was assigning into Context._order from outside;
now Context.resume_numbering.

### R15 · Inappropriate intimacy · geometry/rigs.py · _group_of
closed 2026-08-28 by c2c1adb — private, 3 external callers against 2 internal,
and a test reached past the underscore. Renamed `group_of`.

### R16 · Primitive obsession · yaw normalisation
closed 2026-08-28 by c2c1adb and 41e1bc2 — `round(yaw) % 360` at 9 sites →
`references.bearing()`.

### R17 · Dead code · 49 unused imports, 3 dead locals, 2 loop variables
closed 2026-08-28 by f16479a — `make lint` selects only F821,F811,F502,F506,
F601,F632,B018 and deliberately omits F401, so none of these were visible to it.

### R18 · Comments · 20 truncation artifacts
closed 2026-08-28 by f16479a and c2c1adb — fc2a02d cut the front off 7
docstrings and the back off 13 comments, leaving fragments like "[...] enlarged
image is analysing invented pixels". Each is one readable line now.

### R19 · Long parameter list · generation/comfy.py · sample_and_save
closed 2026-08-28 by 4a31bea — 14 parameters → 9. The eight-key sampling clump
is `comfy.Sampling`, and the two inline defaults that were written at all three
call sites live in `from_config`.

### R20 · Test gap · CanonicalStage.run and FramesStage.run
closed 2026-08-28 by 54f0353 — the two most-changed methods in the tree had no
test executing them, which blocked R1, R2, R19 and R21. FakeComfy records
uploads and graphs; 21 tests, checked by mutation.

### R21 · Bug · frames read a blank style differently from canonical
closed 2026-08-28 by 41e1bc2 — `style: ""` reached the prompt as nothing in
frames (`opt`, missing) and as the default in canonical (`or`, falsy). Both
stages read `subject` with `or`, so frames was the outlier.

---

## Bugs found and fixed

### R31 · queue.submit silently overwrote a same-second duplicate
closed 2026-09-01 by ff31230 — submit now uses shared.files.unique_name, which
already existed for this and is used at three other write sites.
Was:
found 2026-09-01 while writing tests/flows/test_autopilot.py. Two submit() calls
in the same second with the same config name return two Job objects and write
one file: the path is `{priority:04d}_{stamp}_{name}.json` and the disambiguating
suffix only separates matrix cells within a single call. Verified directly —
`submit` twice, 2 Jobs returned, 1 file on disk, `list(PENDING)` reports 1.
Not fixed: a filename change is a stored-format change and needs its own commit
with its own argument.

### R32 · --drain never exited while a job is held
closed 2026-09-01 by ff31230 — a drain that finds nothing ready re-checks the
held jobs, returns any whose wait is over, and exits only when none is. Exiting
straight away would have stranded a job unblocked by a later job in the same run.
Was:
found 2026-09-01 the same way. `if args.drain and not held` means a permanently
held job keeps a drain run alive forever, sleeping on poll. Arguably correct —
a held job may become ready — but it makes `--drain` unable to terminate a
queue containing an unsatisfiable dependency. Recorded rather than changed;
tests/flows/test_autopilot.py pins the current behaviour and says why.

---

## Dropped

### R22 · Long parameter list · definitive/pixelize.py:403 · pixelize
dropped 2026-08-28 — 14 parameters, 12 positional, and that is the answer rather
than the problem. stages/palette.py:20 states it in the code: "Pool worker.
Arguments stay primitive so they pickle cheaply." A parameter object would put a
richer object through a multiprocessing boundary against a recorded decision.

### R23 · Feature envy · shared/guard.py:88 · Guard._kill
dropped 2026-08-28 — 10 accesses to `target` against 2 to `self`, and the
separation is the design. docs/DECISIONS.md:458: the guard "watches from OUTSIDE
and its only action is SIGKILL, because self-restraint fails three ways here and
all three happened". Reading everything about a victim it shares nothing with is
what an external killer looks like.

### R24 · Lazy class · shared/contracts.py:97 · LayerField
dropped 2026-08-28 — an empty subclass whose only import renames it back, but
tests/unit/test_contracts.py:88 asserts the alias identity deliberately and the
module docstring declares "One declaration, three enforcement policies".

### R25 · Data class · looks/training.py:26 Target, definitive/layers.py:98 LayerSpec
dropped 2026-08-28 — Target is a DTO at a serialisation boundary; every field
read is in web/js/views/styles/styles.js after as_dict(). LayerSpec has four
real methods including clamping and the magnify callback.

### R26 · Speculative generality · looks/stylelog.py · archive_training
dropped 2026-08-28 — the unused `keep_thumbnails` parameter promises behaviour
the body does not implement, but docs/OPEN.md §5 owns the function as a
half-built feature and AGENTS.md forbids deleting an unwired capability. The
parameter alone is not worth a commit; it goes when the feature is finished.

### R27 · Bug · shared/files.py:121 · closure over a loop variable
dropped 2026-08-28 — reported as a bug from ruff B023 and it is not one. `field`
was always invoked in the same iteration that bound `disposition`. The closure
was hoisted to `_disposition_field` in 41e1bc2 for shape, not for correctness.

---

## Refused

### R28 · Template Method on Stage · findings R1 and R11
refused 2026-08-28 — only 2 of the 7 stages construct a comfy.Client. Hoisting
the preamble onto Stage hands 5 stages an inherited member they never call,
trading a duplication smell for Refused Bequest.

### R29 · Facade / Strategy over the generation preamble
refused 2026-08-28 — Facade would be the Extract Class of R1 wearing a pattern's
name. Strategy needs a variation and there is none: both stages ran the same
preamble, which is why it was extractable at all.

### R30 · Builder · comfy.sample_and_save
refused 2026-08-28 — Introduce Parameter Object reached it (R19). Builder buys
staged optional construction that a settings bundle read from one config block
does not need.

---

## How to use this

Re-run `refactor-facade` after a stretch of work; it reconciles this file
against the code and reports the delta. Do not re-open anything under `Dropped`
or `Refused` without new evidence — each one records the evidence that closed it.
