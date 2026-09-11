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
RUNS = ROOT / "out/runs"

# A run here is a single GPU job, so cooling.seconds never fires inside one -
# the rest has to sit between runs or the machine works 56 of them back to back.
REST = 120

# ComfyUI's resident size grows across a long sweep - measured at 15 GB of 16
# after roughly thirty runs, with 12.2 GB of swap in use and the machine's own
# pressure gauge reading critical. A restart between batches bounds it; the
# next run waits for the service to come back, which wait_for_comfy already
# does.
RESTART_EVERY = 8


def restart_comfy() -> None:
    from pipeline.shared import guard

    pid = guard.find_service(guard.SERVICES["comfy"])
    if pid is None:
        return
    print(f"  restarting ComfyUI (pid {pid}) to give its memory back", flush=True)
    subprocess.run(["kill", str(pid)], check=False)
    for _ in range(30):
        if guard.find_service(guard.SERVICES["comfy"]) is None:
            break
        time.sleep(1)
    subprocess.Popen([str(ROOT / "start.sh")], cwd=ROOT,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
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
# Round three. The defaults now carry what rounds one and two measured - lora
# 0.8, identity 1.15, style 0.12, background auto - so `shipped` IS the
# baseline and each variant moves one thing off it.
# Round four. `shipped` is the control; the rest are the balance the pixelise
# pass has to strike. Quantising alone reaches block 8 and 83% fill, but it
# compacts detail - the denoise that follows decides how much comes back
# before the model re-invents a finer grid than the sprite has room for.
VARIANTS = {
    "shipped": {},
    "pix_030": {"pixelise": {"denoise": 0.30},
                "pipeline": {"stages": ["pose", "depth", "canonical",
                                        "pixelise"], "stop_after": "pixelise"}},
    "pix_045": {"pixelise": {"denoise": 0.45},
                "pipeline": {"stages": ["pose", "depth", "canonical",
                                        "pixelise"], "stop_after": "pixelise"}},
    "pix_060": {"pixelise": {"denoise": 0.60},
                "pipeline": {"stages": ["pose", "depth", "canonical",
                                        "pixelise"], "stop_after": "pixelise"}},
    # char8_lora_08 was the most pixel-like result recorded - block 3.0 against
    # everything else's 2.0 - and it had hi_fidelity's exemplars. crisp has
    # none. If the exemplars were supplying the RENDERING rather than only the
    # colour, this is where it shows.
    "with_exemplars": {"styles": ["hi_fidelity"],
                       "canonical": {"lora_strength": 0.8, "style_weight": 0.12}},
}


def base(char: str) -> dict:
    """Only what a sweep needs: one front anchor, this character's references.

    Everything else is left to base_pixel and the style sheet on purpose. The
    earlier rounds pinned proportions, background.colour, style_weight and
    style.end_at here, which meant the sweep measured its own settings rather
    than the ones a real run would inherit.
    """
    return {
        "module": "character_sheet",
        "subject": SUBJECTS[char],
        "styles": ["crisp"],
        "props": [],
        "props_enabled": False,
        "pose": {"source": "tpose", "size": 1024, "set": [{"view": "front"}]},
        "canonical": {"candidates": 1},
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


def plan(only: list[str] | None = None) -> list[tuple[str, str, Path]]:
    """Variant-major: every character is reached before any is repeated.

    A sweep this long will be read before it finishes, and character-major
    ordering would spend the first hours on char1 and answer nothing about the
    other seven.
    """
    CONFIGS.mkdir(parents=True, exist_ok=True)
    chars = [c for c in sorted(SUBJECTS)
             if (ROOT / f"library/refs/{c}/front.png").exists()
             and (not only or c in only)]
    out = []
    for name, extra in VARIANTS.items():
        for char in chars:
            cfg = merge(base(char), extra)
            path = CONFIGS / f"{char}_{name}.yaml"
            path.write_text(yaml.safe_dump(cfg, sort_keys=False))
            out.append((char, name, path))
    return out


def scored() -> dict[str, dict]:
    """Every run that carries a score, read from the runs themselves.

    A private results file was a second record of something each run already
    keeps beside its artifacts.json, and only this tool could read it.
    """
    out: dict[str, dict] = {}
    for path in sorted(RUNS.glob("*/score.json")):
        try:
            row = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        variant = row.get("variant")
        char = Path(row.get("reference", "")).parent.name
        if variant and char:
            row["run"] = path.parent.name
            out[f"{char}_{variant}"] = row
    return out


def done() -> set[str]:
    return set(scored())


def score_run(char: str, name: str, run_dir: Path) -> dict | None:
    """Scored in a subprocess: CLIP-ViT-H-14 is 2.35 GB, ComfyUI holds 5.6 of
    the machine's 16, and keeping the encoder resident is what got this killed."""
    out = subprocess.run(
        [str(ROOT / "ComfyUI/.venv/bin/python"), str(ROOT / "tools/score.py"),
         str(ROOT / f"library/refs/{char}/front.png"), str(run_dir)],
        cwd=ROOT, capture_output=True, text=True)
    path = run_dir / "score.json"
    if out.returncode != 0 or not path.exists():
        return None
    row = json.loads(path.read_text())
    row["variant"] = name
    path.write_text(json.dumps(row, indent=1) + "\n")
    return row


def run_all(only: list[str] | None = None, variants: list[str] | None = None) -> int:
    from pipeline.geometry import framing

    # An interrupted emphasis run leaves its maps behind, and every later
    # variant would then be scored with a regional weight it never asked for.
    for char in SUBJECTS:
        clear_emphasis(char)
    already = done()
    jobs = [j for j in plan(only) if f"{j[0]}_{j[1]}" not in already
            and (not variants or j[1] in variants)]
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
        print(f"  {row['id']:26} {row.get('likeness', '-')}  "
              f"bleed {row.get('bleed', '-')}  {row['seconds']}s", flush=True)
        if job is not jobs[-1]:
            if (jobs.index(job) + 1) % RESTART_EVERY == 0:
                restart_comfy()
            time.sleep(REST)
    return 0


def report_table() -> int:
    rows = list(scored().values())
    if not rows:
        print("nothing scored yet")
        return 1

    print(f"\n{len(rows)} scored runs\n")
    print("by variant:")
    for name in VARIANTS:
        mine = [r for r in rows if r.get("variant") == name]
        if not mine:
            continue
        avg = lambda k: sum(r.get(k, 0) for r in mine) / len(mine)  # noqa: E731
        print(f"  {name:16} n={len(mine):2}  likeness {avg('likeness'):.4f}  "
              f"bleed {avg('bleed'):.4f}  block {avg('block'):.2f}")

    print("\nbest variant per character:")
    chars = sorted({Path(r.get("reference", "")).parent.name for r in rows})
    for char in chars:
        mine = sorted((r for r in rows
                       if Path(r.get("reference", "")).parent.name == char),
                      key=lambda r: -r.get("likeness", 0))
        if mine:
            print(f"  {char}: {mine[0].get('variant', '?'):16} "
                  f"{mine[0].get('likeness', 0):.4f}")
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
        rest = sys.argv[2:]
        chars = [a for a in rest if a.startswith("char")]
        picks = [a for a in rest if not a.startswith("char")]
        return run_all(chars or None, picks or None)
    if what == "report":
        return report_table()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
