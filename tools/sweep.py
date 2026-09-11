"""Generate and run one canonical per character per variant, then score them."""

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

# A run here is a single GPU job, so cooling.seconds never fires inside one - the rest.
REST = 120

# ComfyUI holds model weights in CPU RAM between runs by design - it unloads from GPU.
FREE_EVERY = 6

COMFY_WAIT = 30
COMFY_TRIES = 40


def free_comfy(host: str = "http://127.0.0.1:8188") -> bool:
    import urllib.error
    import urllib.request

    body = json.dumps({"unload_models": True, "free_memory": True}).encode()
    request = urllib.request.Request(
        f"{host}/free", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30):
            return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


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

# The file is named for the drawing; the config wants the angle, and there is no named.
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

# Each variant moves ONE thing away from the baseline, so a score difference names a.
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
    # char8_lora_08 was the most pixel-like result recorded - block 3.0 against.
    "with_exemplars": {"styles": ["hi_fidelity"],
                       "canonical": {"lora_strength": 0.8, "style_weight": 0.12}},
}


def base(char: str) -> dict:
    """Only what a sweep needs: one front anchor, this character's references."""
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
    """Variant-major: every character is reached before any is repeated."""
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
    """Every run that carries a score, read from the runs themselves."""
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

    # An interrupted emphasis run leaves its maps behind, and every later variant would.
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
            if (jobs.index(job) + 1) % FREE_EVERY == 0:
                print(f"  freeing ComfyUI's models: {free_comfy()}", flush=True)
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
