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

from pipeline.shared import atomic  # noqa: E402

CONFIGS = ROOT / "library/configs/sweep"
RUNS = ROOT / "out/runs"

# The character sheets you supplied, and the views cut from them.
# Deliberately not under library/refs, because anything there can be
# handed to the model, and not under training_set, which is the LoRA's.
TRUTH = ROOT / "characters"

# A run here is a single GPU job, so cooling.seconds never fires inside one - the rest.
REST = 60

# ComfyUI holds model weights in CPU RAM between runs by design - it unloads from GPU.
FREE_EVERY = 1

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
# Round seven. Strength and block size are monotonically inverse, and 0.8 is
# the chunkiest that has been measured on a real run - ctx_pixref_09 drew
# block 4.0 there against 2.0 everywhere else. Everything at 0.9 and above
# draws a finer grid, which is the direction already rejected by eye, so the
# ladder above 0.8 is gone. 0.6 stays because it is the untested chunky end.
# The LoRA is the only thing that moves. Every reference is the same
# canvas, the same palette and the same keyed backdrop, so a difference
# in the output is the strength and nothing else.
VARIANTS = {
    "px_09": {"_sample": True, "_refs": "px", "_build": True,
                "canonical": {"lora_strength": 0.9,
                              "from_reference": {
                                  "weight": 1.25, "weight_type": "linear"}}},
    "px_10": {"_sample": True, "_refs": "px", "_build": True,
                "canonical": {"lora_strength": 1.0,
                              "from_reference": {
                                  "weight": 1.25, "weight_type": "linear"}}},
    "px_11": {"_sample": True, "_refs": "px", "_build": True,
                "canonical": {"lora_strength": 1.1,
                              "from_reference": {
                                  "weight": 1.25, "weight_type": "linear"}}},
    "px_12": {"_sample": True, "_refs": "px", "_build": True,
                "canonical": {"lora_strength": 1.2,
                              "from_reference": {
                                  "weight": 1.25, "weight_type": "linear"}}},
    "px_13": {"_sample": True, "_refs": "px", "_build": True,
                "canonical": {"lora_strength": 1.3,
                              "from_reference": {
                                  "weight": 1.25, "weight_type": "linear"}}},
}


# Volume per character, read off each one's own sheet. depth.build thickens
# the capsule between two joints; the chest group is depth-only, so none of
# this reaches the pose skeleton.
# Three characters, not eight, for the conditioning arm: the question is how a
# reference is consumed, and that does not need every body in the set.
SAMPLE: tuple[str, ...] = ("char1", "char2", "char3")

BUILDS: dict[str, dict[str, float]] = {
    "char1": {"chest": 1.15, "thigh": 1.00, "torso": 0.95},
    "char2": {"chest": 1.10, "thigh": 0.95, "torso": 1.00},
    "char3": {"chest": 1.25, "thigh": 1.15, "torso": 1.05},
    "char4": {"chest": 1.20, "thigh": 1.05, "torso": 0.95},
    "char5": {"chest": 1.15, "thigh": 1.10, "torso": 1.05},
    "char6": {"chest": 1.30, "thigh": 1.10, "torso": 1.15},
    "char7": {"chest": 1.10, "thigh": 1.00, "torso": 1.00},
    "char8": {"chest": 1.05, "thigh": 0.90, "torso": 0.90},
}


# Measured constraints. A variant that breaks one is a run whose answer is
# already known, so it fails at plan time rather than after four GPU hours.
# Each entry is (predicate, why) and `why` cites what measured it.
PIXREF_LORA_FLOOR = 0.8


def _violations(name: str, extra: dict, cfg: dict) -> list[str]:
    said = []
    lora = cfg["canonical"].get("lora_strength", 0.8)
    if extra.get("_refs") == "px" and lora < PIXREF_LORA_FLOOR:
        said.append(
            f"{name}: a pixelised reference with lora {lora} is measured worse, "
            f"not chunkier - 0.6 drew block 2.5 where 0.8 and 1.0 both drew 4.0. "
            f"Strength stops being inverse once the reference carries the look.")
    ref = cfg["canonical"].get("from_reference") or {}
    if ref.get("weight_type") == "composition" and not extra.get("_allow_comp"):
        said.append(
            f"{name}: weight_type 'composition' writes attention block 3, which "
            f"is the block the pose guide also drives; measured likeness 0.6497, "
            f"the worst on the board, with limbs drawn twice. Set _allow_comp to "
            f"run it anyway.")
    if not cfg["references"]["identity"] and ref.get("weight"):
        said.append(f"{name}: a reference weight with no reference to apply it to")
    return said


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
        wanted = [c for c in chars if c in SAMPLE] if extra.get("_sample") else chars
        for char in wanted:
            cfg = merge(base(char), extra)
            # A variant cannot name the character's own files, so it asks and
            # plan() answers - the same trick the old _paint marker used.
            want = extra.get("_refs")
            if want:
                tail = "" if want is True else f"_{want}"
                cfg["references"]["identity"] = [
                    {"path": f"characters/{char}/{name}{tail}.png", "view": view}
                    for name, view in VIEWS.items()
                    if (TRUTH / char / f"{name}{tail}.png").exists()]
            # Opt-in, not global: ctx_style_09 is five characters into eight,
            # and changing the body under it would confound a variant against
            # itself. Flip this to every variant once the ctx arm reports.
            if extra.get("_build") and char in BUILDS:
                cfg.setdefault("depth", {})["build"] = dict(BUILDS[char])
            # Stated twice, the gate and the stage list disagree: every frames
            # and pixel variant lengthened the list and still stopped at the
            # anchor, so the arm would have rerun canonicals for nine hours.
            cfg["pipeline"]["stop_after"] = cfg["pipeline"]["stages"][-1]
            broke = _violations(name, extra, cfg)
            if broke:
                raise SystemExit("\n".join(broke))
            path = CONFIGS / f"{char}_{name}.yaml"
            # plan() rewrites every config and runs while another
            # process reads them; a half-written file read as empty.
            atomic.write_text(path, yaml.safe_dump(cfg, sort_keys=False))
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


def _attempt(char: str, name: str, cfg: Path) -> tuple[Path, int]:
    """One go at a job, in its own run directory."""
    run_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{char}_{name}"
    out = ROOT / "out/runs" / run_id
    out.mkdir(parents=True, exist_ok=True)
    with (out / "run.log").open("w") as log:
        code = subprocess.run(
            [str(ROOT / "ComfyUI/.venv/bin/python"), "-u", str(ROOT / "run.py"),
             str(cfg), "--run-id", run_id],
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
    return out, code


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
        started = time.time()
        out, code = _attempt(char, name, cfg)
        if code != 0 and not comfy_up():
            # ComfyUI died mid-prompt, so the graph was never drawn. The config is
            # not on trial here; burning the job would score a machine, not a setting.
            print("  ComfyUI went down mid-run; waiting and trying once more",
                  flush=True)
            if wait_for_comfy():
                out, code = _attempt(char, name, cfg)
        run_id = out.name

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

                if not comfy.Client().free_models():
                    print("  could not free ComfyUI's models", flush=True)
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
        print(f"{len(jobs)} runs across {len(VARIANTS)} variants; "
              f"the conditioning arm samples {len(SAMPLE)} characters")
        for char, name, path in jobs[:8]:
            print(f"  {path.relative_to(ROOT)}")
        return 0
    if what == "run":
        # var/sweep.arms is the one place the arm list lives; passing it through
        # a shell, a detach and a background job lost it silently.
        rest = sys.argv[2:]
        if not rest:
            saved = ROOT / "var/sweep.arms"
            rest = saved.read_text().split() if saved.is_file() else []
        chars = [a for a in rest if a.startswith("char")]
        picks = [a for a in rest if not a.startswith("char")]
        return run_all(chars or None, picks or None)
    if what == "report":
        return report_table()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
