# Working in this repository

Instructions for an agent picking this up. Read `DECISIONS.md` next — it holds
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

**Measure before you assert.** This project has a habit, and it is the habit
worth keeping: every non-obvious claim in the code comments is followed by the
measurement that produced it. If you are about to write "this is better",
generate both and look. If you cannot measure it, say that instead.

**A/B with a fixed seed.** Two images that differ in seed *and* in the thing
you are testing tell you nothing. Every comparison holds the seed.

**Do not delete code because nothing references it.** The owner's rule: if it
is a real capability that was merely never wired up, reconnect it. Only code
that is genuinely superseded — same feature, done better elsewhere — is a
removal candidate. `audit/APPJS_INVESTIGATION.md` is what that analysis looks
like.

## Layout

    pipeline/          the library. Stages, rigs, references, styles.
      stage.py         Stage base class, Context, the `opt()` config reader
      runner.py        builds and validates the execution plan
      stages/          one file per stage: pose, depth, canonical, frames,
                       palette, softbody, export
      rigs.py          body plans. 18 of them; the humanoid layout is
                       OpenPose protocol and its joint order must not change
      bodyspace.py     (lateral, depth, height) -> screen, at any yaw
      references.py    typed references: identity / style / pose / palette
      styles.py        style sheets, single-file or foldered
      stylelog.py      append-only per-style history
      training.py      what to train a look on, and whether the staged
                       images will do it
      pixelize.py      the definitive layer: phase, block reduce, palette
      queue.py         the filesystem job queue
    web/               vanilla JS, no build step. One module per tab.
    styles/            the looks. Foldered ones carry their own exemplars,
                       training data and history.
    configs/           pipelines. `_global.yaml` is machine defaults.
    server.py          stdlib HTTP, no framework
    autopilot.py       drains the queue unattended

## Adding things

**A config setting** goes in `pipeline/schema.py`. That is not documentation —
the settings form is generated from it, and a field that is consumed but not
declared is unreachable in the UI. A test enforces that every declared group is
reachable; add your group to `ORDER` in `web/js/settings.js`.

**A stage** subclasses `Stage`, declares `requires` / `produces` / `optional`,
and registers with `@register`. The runner validates the order before anything
runs, so a stage needing an artifact nothing produces is rejected with an
explanation rather than crashing halfway.

**Reading config** always goes through `opt(cfg, key, default)`. A blank YAML
key parses as `None`, and `cfg.get(key, default)` returns that `None` rather
than the default — this was swept across 67 call sites once already.

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
