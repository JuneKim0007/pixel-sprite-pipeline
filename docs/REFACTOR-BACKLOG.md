# Refactor backlog

Surveyed 2026-08-28 · scope `pipeline/ tools/ autopilot.py run.py server.py` · 72 files
Baseline: 560 pytest + 1 xfailed · 120 frontend · `make check` clean · 12,584 lines · 94 comment lines

Ids are permanent. `Done`, `Dropped` and `Refused` keep theirs; the next id is
one past the highest ever issued.

---

## Open

### R4 · Long method · pipeline/stages/frames.py:33 · run
status   planned
evidence 91 statements, depth 4, 191 lines. Down from 107; the remaining bulk
         is the per-frame loop body, not setup.
remedy   Extract Method — the anchor-weighting block and the controlnet block
         → refactor-composing-method
expect   frames.py run under 60 statements
blocked  none. tests/flows/test_generation_stages.py covers this body now.
first seen 2026-08-28

### R5 · Long method · pipeline/stages/canonical.py:58 · run
status   planned
evidence 95 statements, depth 3, 160 lines. Down from 120 after the duplicated
         conditioning came out. `build()` is still a 45-statement closure.
remedy   Extract Method on `build()` → refactor-composing-method
expect   canonical.py run under 60 statements
blocked  none
first seen 2026-08-28

### R6 · Long method · run.py:47 · main
status   planned
evidence 71 statements, 102 lines. The argparse block and the resume block are
         separable; the outdir-base expression is written twice (lines ~85, ~127).
remedy   Extract Method
expect   run.py main under 40 statements, one outdir expression
blocked  none — but no test drives run.main. Characterise first.
first seen 2026-08-28

### R7 · Long method · autopilot.py:98 · work
status   planned
evidence 68 statements, depth 3, 87 lines
remedy   Extract Method
blocked  no test drives autopilot.work. Characterise first.
first seen 2026-08-28

### R8 · Long method · pipeline/definitive/run.py:24 · apply_stack
status   planned
evidence 62 statements, depth 4, 97 lines
remedy   Extract Method
blocked  none — tests/flows/test_pixel_editor.py drives this path
first seen 2026-08-28

### R9 · Long method · 6 more over threshold
status   open
evidence autorig.py:90 fit_humanoid 66 (was 87) · generation/runner.py:92 run 47 ·
         geometry/depthmap.py:54 render_depth 44 · stages/pose.py:73 run 43 ·
         definitive/pixelize.py:69 reduce_blocks 43 · pixelize.py:345
         background_to_alpha 42/depth 5
remedy   Extract Method, case by case
first seen 2026-08-28

### R10 · Nesting only · 3 methods at depth 5
status   open
evidence geometry/rigs.py:556 scale 33 stmts · api/runs.py:36 list_runs 29 ·
         api/jobs.py:71 queue_act 17
remedy   Replace Nested Conditional with Guard Clauses
blocked  queue_act is the one worth doing and the one without coverage: its
         `action` chain sits inside a double loop with a return and a trailing
         raise, so restructuring can reorder Conflict/Invalid/NotFound. Needs a
         characterisation test on the route first. rigs.scale is covered by 6
         tests but buys little alone.
first seen 2026-08-28

### R11 · Shotgun surgery · 7 stage files + generation/stage.py
status   deferred
evidence The stage↔settings contract moved 7 times, each editing 4-7 of the 7
         stage files: 1462901, db13bb5, fce2794, 451ba86, 12a8b85, 97ad0b4,
         65ddf8c. Threshold 3/3; measured 7/7.
remedy   Move Method — settings resolution onto Stage
blocked  docs/OPEN.md §1 owns the general version and defers it. The trend is
         already downward (7 stage files per change → 5) under an in-flight
         campaign; adding a mediator now would freeze a design mid-migration.
         Re-measure after the next settings change.
first seen 2026-08-28

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
