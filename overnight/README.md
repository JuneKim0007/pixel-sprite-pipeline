# Overnight batch — instructions for the agent running it

You are on a machine that is going to generate sprite sheets unattended. Each
`char_N/` directory here is one character, already prepared: references cropped
and labelled, config written, build solved. **Your job is to run them, not to
redesign them.**

Read `../AGENTS.md` for how to work in this repository and `../DECISIONS.md`
before changing any number — several defaults look arbitrary and are not.

---

## Do this

```bash
make up                    # ComfyUI, Ollama, the web UI
make check                 # lint + validate every config. Seconds, no GPU.
```

If `make check` fails here, the clone or the model install is incomplete. Fix
that first; it is not a config problem.

Then, **for the first character only**, gate the run and look at the anchor:

```bash
python3 run.py configs/char_1.yaml --run-id char_1_probe --stop-after canonical
```

Open `out/runs/char_1_probe/02_canonical/canonical.png`. You are checking three
things, and none of them need taste:

| check | what a failure looks like |
|---|---|
| Is it a character at all? | a filled silhouette, a blob with two vertical bars — the model traced the depth map instead of drawing |
| Full body, head to feet? | cropped at the waist |
| One figure, no floating objects? | duplicated limbs, a weapon appearing that nothing configured |

If the anchor is wrong, **stop and report**. Every frame inherits from it, so a
bad anchor is the cheapest thing to catch and the most expensive to let run.
Do not tune your way out of it overnight; say what you saw.

If it is right, queue everything and let the autopilot drain:

```bash
for c in char_1 char_2 char_3; do
  test -f configs/$c.yaml && python3 -c "
import sys; sys.path.insert(0,'.')
from pathlib import Path
from pipeline.queue import Queue
Queue(Path('.')).submit({'config': '$c'}, priority=50)
print('queued $c')"
done

python3 autopilot.py --drain
```

`--drain` exits when the queue empties instead of idling. Alternatively use the
Queue tab in the web UI, which has the same thing behind a button.

---

## Report back with

- Which characters completed, and the run ids.
- Any job in `queue/failed/` — its `.error.txt` sits beside it.
- Anything **held**: that means a dependency was not ready, not that it broke.
- The four view images per character, so they can be looked at.

Do not silently retune settings to make something look better. If a setting
seems wrong, say which one and what you observed. The numbers in these configs
were measured, and `DECISIONS.md` records what by.

---

## What is already decided, so you do not have to

**Views.** Each character's reference sheet is cropped into four labelled
images. The yaw convention here is the pipeline's, not the sheet's wording:
0 faces the viewer and increases clockwise seen from above, so the character's
own left side is 90 and its right side is 270.

**Identity weights.** One reference per view means every frame has a reference
at its own angle, so identity holds at 0.85 all the way round. A single front
drawing would drop to 0.45 on the rear, and the model would invent the back of
the costume differently in every frame.

**Build.** Solved against the depth map rather than set by eye. The capsules
overlap, so a 1.12 multiplier only widened the silhouette by 5%; 1.24 body /
1.02 head measures +11% and +5%, which is what was asked for. `depth.build`
takes a mapping using the same group names as `proportions`.

**Weapons are off.** A character sheet drops props by default. A weapon
occludes the torso and arm it crosses, and is a long rigid volume in the depth
map that the model traces instead of the limb behind it. Weapons belong to the
animation configs, where `props` place them at the hand the geometry knows
about. `char_1`'s reference sheet has a flame staff; it is deliberately absent
from the sheet config.

---

## Cooling, and the GPU limit that does not exist

`cooling.seconds: 420` — seven minutes between GPU tasks.

**Be clear about what this does.** It is a duty cycle, not a utilisation cap.
There is no MPS equivalent of `CUDA_MPS_ACTIVE_THREAD_PERCENTAGE`;
`PYTORCH_MPS_HIGH_WATERMARK_RATIO` limits *memory*, not how hard the GPU is
driven. While a task runs, it runs flat out. What cooling controls is what
fraction of the night is spent running at all:

| rest | duty cycle for a ~5 min task |
|---|---|
| 0 | 100% |
| 2 min | 71% |
| 3 min | 62% |
| 5 min | 50% |
| **7 min** | **42%** |

So seven minutes is *more* conservative than "70% of the GPU" — it is 42% of
wall-clock. If 70% was the actual intent, set `cooling.seconds: 120`. Both are
defensible; they are different requests, and the number in the config is seven
minutes because that is what was asked for explicitly.

The Run tab shows the total resting time before you press start.

---

## Characters

| directory | status |
|---|---|
| `char_1/` | ready — red-veiled priestess. Sheet also shows a flame staff; excluded. |
| `char_2/` | ready — silver-haired knight in a black cape. Sheet also shows a longsword; excluded. |
| `char_3/` | ready — mint-haired elf archer in teal and ice. Sheet also shows a bow and quiver; excluded. |

All three: four views, one labelled reference each, build solved to +11% body
and +5% head, cooling seven minutes, seed pinned. **No weapons in any of
them** — they are drawn by hand afterwards or added to an animation config.

Each holds `refs/` (the cropped views plus `_source_sheet.png`, the original)
and a copy of the config as it was prepared. The live config the pipeline reads
is `configs/char_N.yaml` at the repository root — the copy here is provenance,
so a later edit at the root is visible as a difference.
