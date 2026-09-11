"""Generate and run one canonical per character per variant, then score them."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONFIGS = ROOT / "library/configs/sweep"
RUNS = ROOT / "out/runs"

# The character sheets you supplied, and the views cut from them.
# Deliberately not under library/refs, because anything there can be
# handed to the model, and not under training_set, which is the LoRA's.
TRUTH = ROOT / "characters"

# A run here is a single GPU job, so cooling.seconds never fires inside one - the rest.
REST = 480

# ComfyUI holds model weights in CPU RAM between runs by design - it unloads from GPU.
FREE_EVERY = 6

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
# Round five. pose.set holds one view, so a full run is two GPU jobs rather
# than four and the frames questions are affordable across all eight.
# Round six: the ladder is cut at 1.0. Style reads best at 0.8 and the block
# the model draws on the head did not move between 0.8 and 1.1 on any of the
# eight, so the top of the range was buying nothing. What is left is the
# pixelise question, which is where the cell count actually lives.
VARIANTS = {
    "lora_08": {"canonical": {"lora_strength": 0.8},
                  "frames": {"lora_strength": 0.8}},
    "lora_09": {"canonical": {"lora_strength": 0.9},
                  "frames": {"lora_strength": 0.9}},
    "lora_10": {"canonical": {"lora_strength": 1.0},
                  "frames": {"lora_strength": 1.0}},
    # The context arm. Each reintroduces the identity reference, now square
    # and padded, and moves exactly one thing about how it is consumed.
    # weight_type decides which SDXL attention blocks the adapter writes to:
    # linear all eleven, `style transfer` only block 6, `composition` only 3.
    "ctx_linear_09": {"_refs": True,
                      "canonical": {"from_reference": {
                          "weight": 0.9, "weight_type": "linear"}}},
    "ctx_style_09": {"_refs": True,
                     "canonical": {"from_reference": {
                         "weight": 0.9, "weight_type": "style transfer"}}},
    "ctx_comp_09": {"_refs": True,
                    "canonical": {"from_reference": {
                        "weight": 0.9, "weight_type": "composition"}}},
    # Take the reference's SHAPE and leave its rendering alone: block 3 is
    # layout and structure, block 6 is colour and material. Measured at linear
    # 0.9 the reference lifts likeness 0.61 -> 0.82 and drops the block the
    # model draws from 8.0 to 1.0, which is an illustration, not a sprite.
    "ctx_shape_09": {"_refs": True,
                     "canonical": {"from_reference": {
                         "weight": 0.2, "weight_composition": 0.9,
                         "weight_type": "style and composition"}}},
    "ctx_linear_04": {"_refs": True,
                      "canonical": {"from_reference": {
                          "weight": 0.4, "weight_type": "linear"}}},
    # Frames with no pixelise: the `neither` arm.
    "frames_08": {"canonical": {"lora_strength": 0.8},
                    "frames": {"lora_strength": 0.8},
                    "pipeline": {"stages": ['pose', 'depth', 'canonical', 'frames', 'palette', 'export']}},
    "frames_10": {"canonical": {"lora_strength": 1.0},
                    "frames": {"lora_strength": 1.0},
                    "pipeline": {"stages": ['pose', 'depth', 'canonical', 'frames', 'palette', 'export']}},
    "frames_12": {"canonical": {"lora_strength": 1.2},
                    "frames": {"lora_strength": 1.2},
                    "pipeline": {"stages": ['pose', 'depth', 'canonical', 'frames', 'palette', 'export']}},
    # Scale AND grid, then frames: the `both` arm.
    "pixel_08": {"canonical": {"lora_strength": 0.8},
                   "frames": {"lora_strength": 0.8},
                   "pipeline": {"stages": ['pose', 'depth', 'canonical', 'pixelise', 'frames', 'palette', 'export']}},
    "pixel_10": {"canonical": {"lora_strength": 1.0},
                   "frames": {"lora_strength": 1.0},
                   "pipeline": {"stages": ['pose', 'depth', 'canonical', 'pixelise', 'frames', 'palette', 'export']}},
    "pixel_12": {"canonical": {"lora_strength": 1.2},
                   "frames": {"lora_strength": 1.2},
                   "pipeline": {"stages": ['pose', 'depth', 'canonical', 'pixelise', 'frames', 'palette', 'export']}},
}


def base(char: str) -> dict:
    """Only what a sweep needs: one front anchor, no identity reference."""
    return {
        "module": "character_sheet",
        "subject": SUBJECTS[char],
        "styles": ["crisp"],
        "props": [],
        "props_enabled": False,
        "pose": {"source": "tpose", "size": 1024, "set": [{"view": "front"}]},
        "canonical": {"candidates": 1},
        # No identity reference: prompt, rig and LoRA alone. The reference was
        # carrying its own background, its own arms and a centre crop that cut
        # the head off, and every one of those reached the anchor.
        "references": {"identity": []},
        "pipeline": {"stages": ["pose", "depth", "canonical"]},
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


def plan(only: list[str] | None = None) -> list[tuple[str, str, Path]]:
    """Variant-major: every character is reached before any is repeated."""
    CONFIGS.mkdir(parents=True, exist_ok=True)
    chars = [c for c in sorted(SUBJECTS)
             if (TRUTH / c / "front.png").exists()
             and (not only or c in only)]
    out = []
    for name, extra in VARIANTS.items():
        for char in chars:
            cfg = merge(base(char), extra)
            # A variant cannot name the character's own files, so it asks and
            # plan() answers - the same trick the old _paint marker used.
            if extra.get("_refs"):
                cfg["references"]["identity"] = [
                    {"path": f"characters/{char}/{name}.png", "view": view}
                    for name, view in VIEWS.items()
                    if (TRUTH / char / f"{name}.png").exists()]
            # Stated twice, the gate and the stage list disagree: every frames
            # and pixel variant lengthened the list and still stopped at the
            # anchor, so the arm would have rerun canonicals for nine hours.
            cfg["pipeline"]["stop_after"] = cfg["pipeline"]["stages"][-1]
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
         str(TRUTH / char / "front.png"), str(run_dir)],
        cwd=ROOT, capture_output=True, text=True)
    path = run_dir / "score.json"
    if out.returncode != 0 or not path.exists():
        return None
    row = json.loads(path.read_text())
    row["variant"] = name
    path.write_text(json.dumps(row, indent=1) + "\n")
    return row


LOCK = ROOT / "var" / "sweep.pid"


def already_sweeping() -> int | None:
    """Another sweep's pid, or None. Two at once share one GPU and interleave
    their variant sets - which is how a run from a previous round appeared in
    the middle of this one."""
    if not LOCK.exists():
        return None
    try:
        pid = int(LOCK.read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    return pid if pid != os.getpid() else None


def run_all(only: list[str] | None = None, variants: list[str] | None = None) -> int:
    running = already_sweeping()
    if running:
        print(f"a sweep is already going as pid {running}", flush=True)
        return 1
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(str(os.getpid()))
    try:
        return _run_all(only, variants)
    finally:
        LOCK.unlink(missing_ok=True)


def _run_all(only: list[str] | None = None, variants: list[str] | None = None) -> int:
    from pipeline.geometry import framing

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
        run_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{char}_{name}"
        out = ROOT / "out/runs" / run_id
        out.mkdir(parents=True, exist_ok=True)
        started = time.time()
        with (out / "run.log").open("w") as log:
            code = subprocess.run(
                [str(ROOT / "ComfyUI/.venv/bin/python"), "-u", str(ROOT / "run.py"),
                 str(cfg), "--run-id", run_id],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode

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
                from pipeline.generation import comfy

                print(f"  freeing ComfyUI's models: "
                      f"{comfy.Client().free_models()}", flush=True)
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
