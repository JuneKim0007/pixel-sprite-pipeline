# Working in this repository

Instructions for an agent picking this up. Read `docs/DECISIONS.md` next — it holds
the findings that cost GPU time to establish and that you cannot re-derive by
reading the code.

## What this is

A local pixel-art sprite generator. SDXL plus a pixel-art LoRA, driven through
ComfyUI's HTTP API, wrapped in a staged pipeline with a web UI, a filesystem
job queue and an unattended runner. Everything is local; nothing calls a
hosted model at generation time.

    make up          start ComfyUI, Ollama and the web UI
    make down        stop them
    make check       lint + validate every config (fast, no GPU)
    make test        backend and frontend tests
    make run CONFIG=character_sheet

## The rules that matter

**Never restart services while a generation is in flight.** `make restart`
bounces ComfyUI, and every in-flight run dies with `connection refused` from a
socket that was healthy a second earlier — it does not fail loudly, it fails
looking like a network problem. `scripts/ctl.sh` refuses when ComfyUI has work
queued; do not override it with `FORCE=1` unless you know the queue is stale.

**`make check` before claiming anything works.** It runs `ruff` for undefined
names first, because that defect class compiles cleanly, passes review, and
then raises at runtime six GPU-minutes into a stage. It has happened twice.

**Measure before you assert.** If you are about to write "this is better",
generate both and look. If you cannot measure it, say that instead.

**Put the reasoning in the commit message, not the source.** The measurement
goes in the commit and in `docs/`; a comment gets one line, if any. This rule
used to say the opposite and the source grew to 16% prose — 2,551 lines of
comment and docstring — before it was cut back to 2%. Code with an essay
attached is harder to read, not easier. Delete a comment sooner than shorten it.

**A/B with a fixed seed.** Two images that differ in seed *and* in the thing
you are testing tell you nothing. Every comparison holds the seed.

**Do not delete code because nothing references it.** The owner's rule: if it
is a real capability that was merely never wired up, reconnect it. Only code
that is genuinely superseded — same feature, done better elsewhere — is a
removal candidate.

## Layout

Three lifecycles, three top-level directories. Each gets one backup and
ignore policy, which is the whole reason they are separated.

    library/           authored and versioned. Yours.
      configs/         pipelines. `_global.yaml` is machine defaults;
                       experiments/ holds the A/B fixtures.
      styles/          the looks. Foldered ones carry their own exemplars,
                       training data and history.
      palettes/ poses/ props/
      refs/            reference art (gitignored, not ours to redistribute)
    out/               derived and gitignored. Reproducible.
      runs/<run_id>/   one numbered folder per stage, plus artifacts.json
      exports/<name>/  finished work, filed by asset
      scratch/         one-off experiments
    var/               operational and gitignored. queue/, logs/, overnight/.

    pipeline/          the library, in seven groups
      shared/          depends on no other group. paths, config, errors,
                       registry, settings, files, limits
      generation/      stage.py (Stage, Context), runner.py, schema.py, comfy.py
      stages/          one file per stage: pose, depth, canonical, frames,
                       palette, softbody, export
      geometry/        rigs (18 body plans; humanoid joint order is OpenPose
                       protocol and must not change), bodyspace, props, softbody
      looks/           styles, palettes, poses, vocabulary, stylelog, training
      refs/            typed references, detection, the Ollama client
      definitive/      the pixel editor: layers, cache, pixelize
      orchestration/   queue, artifacts manifest, cooling
      api/             one router per domain; routing.py is the table
    web/               vanilla JS, no build step. One module per tab.
    server.py          stdlib HTTP, no framework
    autopilot.py       drains the queue unattended

    tests/             pytest. Three tiers, by what a failure implicates.
      unit/            one module, no I/O, no server. Fast enough to keep open.
      flows/           one vertical end to end: generate, rig editor, pixel
                       editor, queue, styles, packaging
      api/             HTTP. conftest starts a server on a free port, so
                       `make test` needs nothing running.
      frontend/        node, not pytest. Run by `make test` first.

Every data directory is named in `pipeline/shared/paths.py` and nowhere else.
A test enforces it.

## Tests

`make test` runs everything. `make test T=tests/unit/test_rigs.py` or
`T='-k dragon'` narrows it; `make test-fast` is the unit tier only.

Put a test in `unit/` if a failure names one module, in `flows/` if it names a
journey, in `api/` if it needs HTTP. Prefer `@parametrize` over a loop inside
one test: the failure then names the case, which is the whole point of the
split.

Two things this suite has been burned by, so do not reintroduce them:

**Assert behaviour, not source text.** A test that greps the code with `ast` or
`inspect.getsource` fails on a reformat and passes on the same bug written
differently. The exceptions are the two architectural invariants in
`flows/test_packaging.py`, which have no runtime surface to check.

**Never write a custom assert helper.** The old harness used `if cond is False`,
so every `numpy` comparison — `np.bool_(False)` is not the `False` singleton —
was silently inert. Two assertions were false for months. Use bare `assert`.

## Adding things

**A config setting** goes in `pipeline/generation/schema.py`. That is not
documentation — the settings form is generated from it, and a field that is
consumed but not declared is unreachable in the UI.

**A default** goes in the stage's `DEFAULTS` dict and nowhere else.
`Context.stage_config` layers it under the config block and `schema.py` reads
it for display, so one declaration serves both. A test fails the build if a
stage restates a default it already declares. Defaults that are *computed*
(`8 if lcm else 25`) cannot be static and stay inline via `opt()`.

**A stage** subclasses `Stage`, declares `requires` / `produces` / `optional`,
and registers with `@register`. The runner validates the order before anything
runs, so a stage needing an artifact nothing produces is rejected with an
explanation rather than crashing halfway.

**Work that does not depend on the loop goes in `prepare(ctx)`**, whose result
the runner hands to `run(ctx, prep)`. It runs once. The palette stage searched
the pixel lattice once per frame until this existed — measured 17.5s of a
12-frame run, for an answer that was identical every time.

**Reading config** goes through `ctx.stage_config(name)`, which drops blank
YAML keys so the declared default applies. Reading a bare dict goes through
`opt(cfg, key, default)` for the same reason: a blank key parses as `None`, and
`cfg.get(key, default)` hands back that `None` rather than the default.

**Prompt words** go in `pipeline/looks/vocabulary.py`; **model filenames and
machine settings** go in `pipeline/shared/settings.py`. Those two are the split
between what a user customises per asset and what they set once per machine.

## Things that look like bugs and are not

- The same canonical sprite is fed to every frame. That is the consistency
  mechanism, not a copy-paste error: only the skeleton is allowed to vary.
- `from . import stages` looks unused in several modules. It populates the
  stage registry; deleting it made every queued job fail validation.
- Style exemplars run at roughly a third of the identity weight. At equal
  weight the exemplar replaces the character.
- Reference weight *falls* as the viewing angle diverges. Constraint where
  there is evidence, latitude where there is not.

## When you finish something

Commit with a message that says what was measured, not just what changed. The
git history is the only place the reasoning survives once the code reads as
though it were always that way.
