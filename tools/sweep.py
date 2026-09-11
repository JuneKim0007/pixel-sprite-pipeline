"""Generate and run one canonical per character per variant, then score them.

    python tools/sweep.py plan          write the configs and print the plan
    python tools/sweep.py run           run everything not yet run, scoring as it goes
    python tools/sweep.py report        rebuild the leaderboard from what exists

Each run is a single front anchor so a variant costs one GPU job, and every
output is scored against the character's own front reference.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONFIGS = ROOT / "library/configs/sweep"
RESULTS = ROOT / "var/sweep.jsonl"

# A run here is a single GPU job, so cooling.seconds never fires inside one -
# the rest has to sit between runs or the machine works 56 of them back to back.
REST = 120
COMFY_WAIT = 30
COMFY_TRIES = 40


def comfy_up(host: str = "http://127.0.0.1:8188") -> bool:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{host}/system_stats", timeout=5):
            return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def wait_for_comfy() -> bool:
    """ComfyUI going down overnight turned every remaining run into a 2 second
    failure, so a crash at run five silently consumed the other thirty-one."""
    for attempt in range(COMFY_TRIES):
        if comfy_up():
            return True
        print(f"  waiting for ComfyUI ({attempt + 1}/{COMFY_TRIES})", flush=True)
        time.sleep(COMFY_WAIT)
    return False

# The file is named for the drawing; the config wants the angle, and there is
# no named view for the far side - `side` is 90, so its mirror is 270.
VIEWS = {"front": "front", "side": "side", "side_right": 270, "rear": "rear"}

SUBJECTS = {
    "char1": "a young woman with long straight black hair, a white shirt with a "
             "red necktie, a black jacket, a black pleated skirt, black knee "
             "socks and black loafers",
    "char2": "an elf woman with pale mint green hair tied up, a dark teal robe "
             "with gold trim, a layered shoulder cape and black boots",
    "char3": "a dark skinned woman with long black hair, a deep red veil and "
             "headscarf, a red and gold layered dress and gold sandals",
    "char4": "a woman with very long pink hair, a cream off the shoulder blouse, "
             "purple belt straps, purple thigh socks and white knee boots",
    "char5": "a woman with long wavy silver green hair, a flower crown, a cream "
             "kimono blouse, a navy floor length skirt and sandals",
    "char6": "a dark skinned man with a white head veil, bare chest, navy and "
             "gold patterned wrap trousers, gold jewellery and brown boots",
    "char7": "a woman with long silver white hair, a black tactical vest, an "
             "olive cape, black thigh socks and black boots",
    "char8": "a woman with long black hair, dark red and navy armour, a red "
             "cape and navy thigh boots",
}

# Each variant moves ONE thing away from the baseline, so a score difference
# names a cause. Measured at 510s a run, seven variants needed 11.7 hours and
# would not have finished; these five are the ones whose answer is not already
# known. `wide` and `hold_control` were both run against experiment_slim, and
# `no_style` is the degenerate end of `identity_led`.
VARIANTS = {
    "baseline": {},
    "no_key": {"background": {"enabled": False}},
    "identity_led": {"canonical": {"from_reference": {"weight": 1.15},
                                   "style_weight": 0.12}},
    "style_led": {"canonical": {"from_reference": {"weight": 0.6},
                                "style_weight": 0.5,
                                "style": {"end_at": 0.9}}},
    "emphasis": {"_paint": True},
}


def base(char: str) -> dict:
    return {
        "module": "character_sheet",
        "subject": SUBJECTS[char],
        "style": "pixel art, game sprite",
        "styles": ["hi_fidelity"],
        "props": [],
        "props_enabled": False,
        "proportions": {"legs": 1.15, "torso": 1.1},
        "background": {"colour": "242, 94, 147"},
        "pose": {"source": "tpose", "size": 1024, "fill": 0.69,
                 "margin": 0.14, "set": [{"view": "front"}]},
        "canonical": {"candidates": 1, "style": {"end_at": 0.65},
                      "style_weight": 0.28},
        "references": {"identity": [
            {"path": f"library/refs/{char}/{name}.png", "view": view}
            for name, view in VIEWS.items()
            if (ROOT / f"library/refs/{char}/{name}.png").exists()
        ]},
        "pipeline": {"stages": ["pose", "depth", "canonical"],
                     "stop_after": "canonical"},
        "cooling": {"enabled": True, "seconds": 60},
    }


def merge(into: dict, extra: dict) -> dict:
    for key, value in extra.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and isinstance(into.get(key), dict):
            merge(into[key], value)
        else:
            into[key] = value
    return into


def paint_emphasis(char: str) -> None:
    """A map that says 'the figure, not the ground', from the reference itself."""
    import numpy as np
    from PIL import Image

    from pipeline.geometry import weightmap
    from tools.score import subject

    for name in VIEWS:
        image = ROOT / f"library/refs/{char}/{name}.png"
        if not image.exists():
            continue
        _, mask = subject(image)
        small = np.asarray(Image.fromarray((mask * 255).astype("uint8"))
                           .resize((weightmap.EDGE, weightmap.EDGE), Image.BILINEAR))
        weights = 0.45 + 0.55 * (small.astype("float32") / 255.0)
        weightmap.save(image, weights)


def clear_emphasis(char: str) -> None:
    from pipeline.geometry import weightmap

    for name in VIEWS:
        image = ROOT / f"library/refs/{char}/{name}.png"
        if image.exists():
            weightmap.clear(image)


def plan() -> list[tuple[str, str, Path]]:
    """Variant-major: every character is reached before any is repeated.

    A sweep this long will be read before it finishes, and character-major
    ordering would spend the first hours on char1 and answer nothing about the
    other seven.
    """
    CONFIGS.mkdir(parents=True, exist_ok=True)
    chars = [c for c in sorted(SUBJECTS)
             if (ROOT / f"library/refs/{c}/front.png").exists()]
    out = []
    for name, extra in VARIANTS.items():
        for char in chars:
            cfg = merge(base(char), extra)
            path = CONFIGS / f"{char}_{name}.yaml"
            path.write_text(yaml.safe_dump(cfg, sort_keys=False))
            out.append((char, name, path))
    return out


def done() -> set[str]:
    """Only runs that actually SCORED count as done.

    A run killed part-way still appended its row, and treating that as done
    meant an interrupted sweep skipped the character it was interrupted on.
    """
    if not RESULTS.exists():
        return set()
    rows = [json.loads(line) for line in RESULTS.read_text().splitlines() if line]
    return {r["id"] for r in rows if "likeness" in r}


def score_run(char: str, name: str, run_dir: Path) -> dict | None:
    """Scored in a subprocess: CLIP-ViT-H-14 is 2.35 GB, ComfyUI holds 5.6 of
    the machine's 16, and keeping the encoder resident for the whole sweep is
    what put the machine under memory pressure and had the sweep killed."""
    made = sorted(run_dir.glob("*_canonical/canonical*.png"))
    if not made:
        return None
    out = subprocess.run(
        [str(ROOT / "ComfyUI/.venv/bin/python"), str(ROOT / "tools/score.py"),
         str(ROOT / f"library/refs/{char}/front.png"), str(made[0])],
        cwd=ROOT, capture_output=True, text=True)
    for line in reversed(out.stdout.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    return None


def run_all() -> int:
    from pipeline.geometry import framing

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    # An interrupted emphasis run leaves its maps behind, and every later
    # variant would then be scored with a regional weight it never asked for.
    for char in SUBJECTS:
        clear_emphasis(char)
    already = done()
    jobs = [j for j in plan() if f"{j[0]}_{j[1]}" not in already]
    print(f"{len(jobs)} to run, {len(already)} already scored", flush=True)

    for job in jobs:
        char, name, cfg = job
        if not wait_for_comfy():
            print("ComfyUI never came back; stopping with the sweep resumable",
                  flush=True)
            return 1
        painted = VARIANTS[name].get("_paint")
        if painted:
            paint_emphasis(char)
        run_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{char}_{name}"
        out = ROOT / "out/runs" / run_id
        out.mkdir(parents=True, exist_ok=True)
        started = time.time()
        with (out / "run.log").open("w") as log:
            code = subprocess.run(
                [str(ROOT / "ComfyUI/.venv/bin/python"), "-u", str(ROOT / "run.py"),
                 str(cfg), "--run-id", run_id],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
        if painted:
            clear_emphasis(char)

        row = {"id": f"{char}_{name}", "char": char, "variant": name,
               "run": run_id, "exit": code, "seconds": round(time.time() - started)}
        scored = score_run(char, name, out) if code == 0 else None
        if scored:
            row.update(scored)
            made = sorted(out.glob("*_canonical/canonical*.png"))[0]
            box = framing.measure(made)
            row["clipped"] = ",".join(box.clipped) if box else "?"
        with RESULTS.open("a") as handle:
            handle.write(json.dumps(row) + "\n")
        print(f"  {row['id']:26} {row.get('likeness', '-')}  "
              f"bleed {row.get('bleed', '-')}  {row['seconds']}s", flush=True)
        if job is not jobs[-1]:
            time.sleep(REST)
    return 0


def report_table() -> int:
    if not RESULTS.exists():
        print("nothing run yet")
        return 1
    rows = [json.loads(line) for line in RESULTS.read_text().splitlines() if line]
    scored = [r for r in rows if "likeness" in r]

    print(f"\n{len(scored)} scored of {len(rows)} run\n")
    print("by variant, mean likeness to the character's own reference:")
    for name in VARIANTS:
        mine = [r for r in scored if r["variant"] == name]
        if not mine:
            continue
        like = sum(r["likeness"] for r in mine) / len(mine)
        bled = sum(r["bleed"] for r in mine) / len(mine)
        cut = sum(1 for r in mine if r.get("clipped"))
        print(f"  {name:14} likeness {like:.4f}  bleed {bled:.3f}  "
              f"clipped {cut}/{len(mine)}")

    print("\nbest variant per character:")
    for char in sorted({r["char"] for r in scored}):
        mine = sorted((r for r in scored if r["char"] == char),
                      key=lambda r: -r["likeness"])
        if mine:
            top = mine[0]
            print(f"  {char}: {top['variant']:14} {top['likeness']:.4f}")
    return 0


def main() -> int:
    what = sys.argv[1] if len(sys.argv) > 1 else "plan"
    if what == "plan":
        jobs = plan()
        print(f"{len(jobs)} runs: {len({j[0] for j in jobs})} characters "
              f"x {len(VARIANTS)} variants")
        for char, name, path in jobs[:8]:
            print(f"  {path.relative_to(ROOT)}")
        return 0
    if what == "run":
        return run_all()
    if what == "report":
        return report_table()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
