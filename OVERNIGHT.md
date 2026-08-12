# Running a batch on another machine

For the case this was built for: sit down with a few reference images, set up
several characters, start it, and go to bed.

Everything here is checked. The queue, the autopilot and the automatic rigging
were each exercised end to end before this was written.

---

## Once, on the new machine

```bash
git clone git@github.com:JuneKim0007/pixel-sprite-pipeline.git
cd pixel-sprite-pipeline
```

The repository does **not** carry ComfyUI or the model weights — 21 GB of the
22 GB working directory is those, and they are reinstallable. Follow
`docs/PROJECT.md` for that part, then:

```bash
make up          # ComfyUI, Ollama, the web UI
make check       # lint + validate every config. Fast, no GPU.
```

`make check` failing here means the clone is incomplete, not that a config is
wrong — fix that before spending a night on it.

---

## Per character

### 1. Drop the references in

```bash
cp ~/wherever/*.png inputs/
```

**Weapon-free is worth arranging.** Identity runs through IP-Adapter at 0.80
and IP-Adapter carries content, not only style, so a bow in the reference
becomes a bow in the output whether or not anything placed one — measured, and
it came back with three of them. Weapons belong to the animation, where `props`
put them at the hand the geometry actually knows about.

**Anime or pixel-art references land better than painterly ones**, for the same
reason. Identity is 0.80 against a style exemplar's 0.18: a photoreal or
thickly-painted reference pulls the rendering with it, and the style sheet
cannot outvote it four to one. A reference already in a flat, line-defined
idiom is not fighting the target.

### 2. Rig it, or do not

**Humanoid figures rig themselves.** Measured on three real references: 14 of
18 joints placed, confidence 84–98%, with limb proportions measured as a
by-product — one reference read `arms 1.44, neck 0.45`, another `torso 1.2,
arms 1.61`. From the Run tab, "Annotate reference" → the fit opens for review.
A wrong fit costs a glance, not six GPU-minutes.

**Non-humanoids do not.** A dragon, spider or serpent returns only a bounding
box and an aspect ratio. The code says why: a spider's width profile does not
say which lobe is which leg. This is fine for a character sheet, which wants
the rig's own A-pose rather than the reference's pose — the reference is
supplying identity, not composition.

You only need an annotation when you want to **reproduce** the reference's
pose. For that, set `pose.source: annotation`.

### 3. Write the config

Copy the nearest one. A config should hold **who the character is** and nothing
about how it should look — that lives in the style sheet.

```yaml
name: my_character
module: character_sheet        # drops weapons by default; see below
rig: humanoid                  # or auto, quadruped, dragon, spider, …
styles: [hi_fidelity]
subject: >
  a young woman archer with short pale cyan hair, a white cropped jacket,
  black thigh-high socks, chunky black boots

references:
  identity:
    - {path: inputs/archer.png, view: front}
    # A rear reference nearly doubles the identity weight on rear frames:
    # 0.45 without, 0.85 with. Label it honestly - a front image labelled
    # `rear` is worse than no rear reference at all.
  style: []
  pose: []
  palette: []

canonical: {seed: 41207, candidates: 2}
```

Then `make check` — it validates every config in seconds and will not let a
broken one reach the queue.

### 4. Queue and go

From the **Queue** tab: pick the config, submit, press **Start and drain**.
Drain means it exits when the queue empties rather than idling all night.

Or from a shell:

```bash
python3 autopilot.py --drain
```

A `matrix` crosses values into separate jobs — three seeds by two views is six
jobs from one form, which is how you spend a night finding a seed you like.

---

## What the autopilot does and does not do

It **does** distinguish a job that can never work from one that is merely
early. A broken config fails immediately; a job waiting on a run that has not
finished is *held* and retried; a dead ComfyUI pauses the worker and blames no
job. That last one survived a real outage with 200 jobs queued.

It **does not** notice that two hundred jobs all produced ugly sprites. Those
complete successfully. Nothing about running unattended removes the need to
look at the first one before queueing the rest.

**Check the first result before going to bed.** Gate a run with
`pipeline.stop_after: canonical`, look at the anchor, and only then queue the
rest — every frame inherits from that anchor, so it is the cheapest thing to be
wrong about and the most expensive to leave wrong.

---

## Settings worth knowing before a long run

| setting | what it is for |
|---|---|
| `cooling.seconds` | Rest between GPU tasks, 180 by default. Nothing needs it to work — it is there so a night of generation does not hold the machine at its throttle point. At 180 s a fifty-image night spends two and a half hours resting; the Run tab shows the total before you start. |
| `canonical.timeout` | Four hours per image. Generous on purpose: a hung job holding the queue costs the whole night, and a run that dies at 30 minutes costs one job. |
| `canonical.candidates` | The best value per GPU-minute. Every frame inherits the anchor, so improving it once improves everything. |
| `depth.build` | How heavy the creature is, as distinct from how tall. `proportions` lengthens bones; this widens the depth capsules, which is the only thing that says a character is broad. A mapping works: `{torso: 1.6, arms: 0.9}`. |
| `props.enabled` | Off for a sheet, on for an animation, automatically. Override either way. |

`CONFIGURING.md` covers the rest, including how to move to 32×32 or 64×64.

---

## If you come back to failures

```bash
python3 autopilot.py --status
```

Failed jobs keep an `.error.txt` beside them in `queue/failed/`. The Queue tab
shows the same thing with a retry button per job.

The two failure modes that look alike and are not:

- **failed** — the config or its inputs are wrong. Fix and retry.
- **held** — a dependency has not been produced yet. It retries itself; if
  everything is held, the run it depends on never finished.

A service that went down pauses the worker instead of failing anything, so a
queue that stopped early with nothing failed usually means ComfyUI died. `make
status` will say.
