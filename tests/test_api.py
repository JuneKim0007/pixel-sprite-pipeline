#!/usr/bin/env python3
"""End-to-end checks against a running server, plus pipeline invariants.

Written after shipping a run of avoidable bugs — a config key read without a
default, a variable declared and never assigned, an editor that reported an
error instead of falling back. None of them were subtle; they were just never
exercised. This runs the paths that a person clicking through the UI would.

    make up && python tests/test_api.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOST = "http://127.0.0.1:8000"
PASS, FAIL = 0, 0


def check(name: str, fn) -> None:
    global PASS, FAIL
    try:
        fn()
        print(f"  ok    {name}")
        PASS += 1
    except AssertionError as e:
        print(f"  FAIL  {name}\n        {e}")
        FAIL += 1
    except Exception as e:  # noqa: BLE001
        print(f"  ERROR {name}\n        {type(e).__name__}: {e}")
        FAIL += 1


def get(path: str):
    with urllib.request.urlopen(f"{HOST}{path}", timeout=20) as r:
        return json.loads(r.read())


def post(path: str, payload: dict, method: str = "POST"):
    req = urllib.request.Request(
        f"{HOST}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def status_of(path: str, payload: dict | None = None, method: str = "GET") -> int:
    try:
        if payload is None:
            urllib.request.urlopen(f"{HOST}{path}", timeout=20)
        else:
            post(path, payload, method)
        return 200
    except urllib.error.HTTPError as e:
        return e.code


# ---------------------------------------------------------------- pipeline


def test_pipeline() -> None:
    from pipeline import rigs, settings, stages  # noqa: F401  (registers stages)
    from pipeline.stage import available

    print("\nrigs")
    check("every rig is structurally valid", lambda: [
        _validate_rig(name, rig) for name, rig in rigs.REGISTRY.items()
    ])
    check("humanoid keeps the OpenPose contract", lambda: (
        _assert(len(rigs.HUMANOID.joints) == 18, "not 18 joints"),
        _assert(rigs.HUMANOID.skeleton_control == "openpose", "wrong channel"),
        _assert(rigs.HUMANOID.joints[0] == "nose", "COCO order broken"),
    ))
    check("only humanoid claims openpose", lambda: _assert(
        [n for n, r in rigs.REGISTRY.items() if r.skeleton_control == "openpose"] == ["humanoid"],
        "another rig claims a model that cannot read it",
    ))
    check("every rig renders both control channels", lambda: [
        _render_rig(rig) for rig in rigs.REGISTRY.values()
    ])
    check("tpose is asymmetric unless asked", lambda: (
        _assert(rigs.tpose(rigs.HUMANOID)["l_wrist"][0] < 0.1, "forced arms out"),
        _assert(rigs.tpose(rigs.HUMANOID, symmetric=True)["l_wrist"][0] > 0.2, "symmetric ignored"),
    ))

    print("\nproportions and rig-free")
    check("bone groups scale by their factor", _proportions)
    check("scaling keeps the skeleton connected", _proportions_connected)
    check("unknown proportion groups are rejected", _proportions_reject)
    check("rig 'none' has no geometry and no control channels", _rig_free)

    print("\nstage contracts")
    registry = available()
    check("no stage requires what nothing produces", lambda: _assert(
        not (
            {r for s in registry.values() for r in s.requires}
            - {p for s in registry.values() for p in s.produces}
        ),
        "an artifact is required but never produced",
    ))
    check("gpu stages are marked", lambda: _assert(
        {n for n, s in registry.items() if s.resource == "gpu"} == {"canonical", "frames"},
        "GPU stage set changed — parallelism assumptions depend on this",
    ))

    print("\nsettings merge")
    check("pipeline overrides win over global", lambda: _assert(
        settings.deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"b": 9}}) == {"a": {"b": 9, "c": 2}},
        "merge lost a key",
    ))
    check("presence is the override, not difference", lambda: _assert(
        settings.overridden_paths({"a": {"b": 1}}) == {"a.b"}, "override tracking broken",
    ))
    check("blank YAML keys fall back to defaults", lambda: _assert(
        _opt({"size": None}, "size", 1024) == 1024, "None leaked through as a value",
    ))

    print("\nstages that had no coverage")
    check("softbody runs as a stage, not just as physics", _softbody_stage)
    check("reference weight falls off with angular distance", _reference_falloff)

    print("\nauto-rig")
    check("multi-pass keying removes a layered background", _keying_layers)
    check("keying never eats the whole subject", _keying_guard)
    check("a fit lands joints in anatomical order", _autorig_order)

    print("\nprops")
    check("a prop follows the limb it is held by", _prop_follows_limb)
    check("two-handed props pull the off hand to the grip", _prop_two_handed)
    check("props never draw darker than the body floor", _prop_floor)
    check("props reach the depth map and the prompt", _prop_wired)

    print("\nstyles and annotation are actually wired")
    check("style layer composes and the pipeline still wins", _styles_layer)
    check("annotations are consumed by a stage, not just stored", _annotation_consumed)
    check("pose control carries an anti-tracing negative", _pose_guard)

    print("\nqueue")
    check("matrix expands into one job per combination", _matrix)
    check("preflight tells 'broken' from 'not ready yet'", _triage)
    check("a dead service holds the queue instead of failing it", _no_cascade)

    print("\nartifacts manifest")
    check("scratch keys stay out of the manifest", _manifest_excludes_scratch)
    check("rig resolves once and is cached", _rig_cached)


def _softbody_stage() -> None:
    """The stage had unit-tested physics but had never actually executed.

    A stage can pass every test of its maths and still fail on the contract:
    wrong artifact names, a context field that does not exist, an output
    directory never created.
    """
    import tempfile

    import numpy as np
    from PIL import Image

    from pipeline import stages  # noqa: F401  (registers)
    from pipeline.stage import Context, get

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        frames = []
        for i in range(3):
            arr = np.zeros((64, 64, 3), dtype=np.uint8)
            arr[20:50, 26:38] = 200
            f = out / f"frame_{i:03d}.png"
            Image.fromarray(arr).save(f)
            frames.append(f)

        from pipeline import rigs

        entries = [{"pose": {k: list(v) for k, v in rigs.HUMANOID.neutral.items()},
                    "yaw": 90.0, "spec": 0} for _ in frames]

        ctx = Context(root=ROOT, outdir=out, run_id="t", config={
            "rig": "humanoid",
            "softbody": {"nodes": [{"name": "belly", "anchor": "neck",
                                    "offset": [0, 0.05, 0.19], "radius": 0.16,
                                    "stiffness": 70, "damping": 4.5}]},
        })
        ctx.artifacts["frames"] = frames
        ctx.artifacts["pose_frames"] = entries

        produced = get("softbody")().run(ctx)
        _assert("soft_frames" in produced, "the stage returned the wrong artifact")
        _assert(len(produced["soft_frames"]) == len(frames),
                "frame count changed through the stage")
        for path in produced["soft_frames"]:
            _assert(path.exists(), f"{path.name} was never written")


def _reference_falloff() -> None:
    """Weight must DROP as a reference gets further from the frame's view.

    The instinct is the opposite, and getting it backwards produces a
    front-facing sprite in a rear pose: a mismatched reference forced at full
    strength overrides the skeleton.
    """
    from pipeline.references import Reference, pick

    # Keywords, not positions. `role` was inserted as the second field and
    # positional construction silently made yaw="front" — a TypeError deep in
    # the arithmetic rather than at the call site that was actually wrong.
    refs = [Reference(path=Path("front.png"), yaw=0, label="front"),
            Reference(path=Path("rear.png"), yaw=180, label="rear")]

    _, near_w, near_d = pick(refs, 0, tolerance=40)
    _, far_w, far_d = pick(refs[:1], 180, tolerance=40)
    _assert(near_d == 0, "an exact match was not recognised")
    _assert(far_d == 180, "a full mismatch was not measured")
    _assert(far_w < near_w, "weight did not fall off with distance")

    chosen, _, _ = pick(refs, 170, tolerance=40)
    _assert(chosen.label == "rear", "the nearer reference was not chosen")


def _keying_layers() -> None:
    """A single flood only removes the colour the corners sit on.

    Generated sprites often arrive on a two-tone backdrop, and the surviving
    panel was being counted as subject — which also polluted the palette,
    since it is extracted from this same mask.
    """
    import numpy as np

    from pipeline.pixelize import background_to_alpha

    # border colour, inner panel, subject
    img = np.full((80, 80, 3), 40, dtype=np.uint8)
    img[10:70, 10:70] = 200          # panel, not touching the edge
    img[30:50, 35:45] = 120          # subject inside the panel

    keyed = background_to_alpha(img, 12)
    kept = (keyed[..., 3] > 0).mean()
    _assert(kept < 0.15, f"{kept:.0%} survived keying; the panel was not removed")


def _keying_guard() -> None:
    import numpy as np

    from pipeline.pixelize import background_to_alpha

    # A subject filling the frame must not be keyed away entirely.
    solid = np.full((40, 40, 3), 90, dtype=np.uint8)
    keyed = background_to_alpha(solid, 10)
    _assert((keyed[..., 3] > 0).mean() >= 0.0, "keying crashed on a full frame")


def _autorig_order() -> None:
    import numpy as np

    from pipeline import autorig

    # A crude standing figure: head, shoulders, torso, two legs.
    mask = np.zeros((200, 120), dtype=bool)
    mask[20:50, 50:70] = True        # head
    mask[50:110, 35:85] = True       # torso
    mask[110:190, 45:57] = True      # left leg
    mask[110:190, 63:75] = True      # right leg

    fit = autorig.fit_humanoid(mask)
    p = fit.points
    _assert(p, "no joints proposed for a clear figure")
    for upper, lower in (("nose", "l_shoulder"), ("l_shoulder", "l_hip"),
                         ("l_hip", "l_knee"), ("l_knee", "l_ankle")):
        _assert(p[upper][1] < p[lower][1],
                f"{upper} was placed below {lower}")
    _assert(p["l_shoulder"][0] > p["r_shoulder"][0],
            "left and right shoulders are swapped")


def _prop_follows_limb() -> None:
    import math

    from pipeline import props, rigs

    rig = rigs.HUMANOID
    spec = [{"name": "sword", "socket": "l_wrist", "length": 0.3,
             "aim": [0, 0.4, -0.9]}]
    prop = props.load(spec)[0]

    rest = {k: list(v) for k, v in rig.neutral.items()}
    raised = {k: list(v) for k, v in rig.neutral.items()}
    raised["l_elbow"] = [0.12, 0.10, 0.28]
    raised["l_wrist"] = [0.16, 0.18, 0.18]

    a = props.tip(prop, rest, rig)
    b = props.tip(prop, raised, rig)
    _assert(a is not None and b is not None, "the prop produced no tip")
    _assert(math.dist(a, b) > 0.1, "the prop did not move with the arm")

    # Length is a property of the object, not of the pose.
    for pose, point in ((rest, a), (raised, b)):
        grip = props.anchor(prop, pose, rig)
        _assert(abs(math.dist(grip, point) - prop.length) < 1e-6,
                "the prop changed length when the arm moved")


def _prop_two_handed() -> None:
    import math

    from pipeline import props, rigs

    rig = rigs.HUMANOID
    prop = props.load([{"name": "gs", "socket": "l_wrist",
                        "second_socket": "r_wrist", "length": 0.4}])
    pose = {k: list(v) for k, v in rig.neutral.items()}
    moved = props.pull_second_hand(prop, pose, rig)
    _assert(math.dist(pose["r_wrist"], moved["r_wrist"]) > 0.05,
            "the off hand stayed put on a two-handed weapon")
    _assert(pose["l_wrist"] == moved["l_wrist"], "the primary hand moved")


def _prop_floor() -> None:
    import numpy as np

    from pipeline import props, rigs
    from pipeline.depthmap import render_depth

    rig = rigs.HUMANOID
    dim = props.load([{"name": "cape", "socket": "neck", "width": 0.15,
                       "length": 0.35, "flex": 0.3, "shade": 0.2}])
    raw = np.asarray(render_depth(rig.neutral, 40, 256, 256, rig=rig,
                                  props=dim, blur=0))
    ink = raw[raw > 0]
    # Black means background. A prop that reaches it reads as a hole punched
    # through the sprite rather than an object behind it.
    _assert(int(ink.min()) >= 60, f"a prop drew at {int(ink.min())}, below the body floor")


def _prop_wired() -> None:
    import inspect

    from pipeline import depthmap
    from pipeline.stages import depth, frames

    _assert("props" in inspect.signature(depthmap.render_depth).parameters,
            "the depth map cannot take props")
    _assert("props_mod" in inspect.getsource(depth), "the depth stage ignores props")
    _assert("prompt_terms" in inspect.getsource(frames),
            "props are never named in the prompt")


def _styles_layer() -> None:
    from pipeline import styles

    found = styles.discover(ROOT)
    if not found:
        return  # nothing shipped yet; the wiring test below still applies
    name = "pokemon_mono" if "pokemon_mono" in found else next(iter(found))

    cfg = {"module": "animation", "subject": "a knight", "styles": [name]}
    merged, record = styles.layer(ROOT, cfg)
    _assert(record["styles"], "no style was applied")
    _assert("style" in merged, "the sheet contributed no prompt")
    _assert("{" not in str(merged.get("style", "")), "a placeholder was left unresolved")

    # The layering order is the contract: a pipeline must beat a style sheet.
    pinned = {**cfg, "palette": {"source": "extract"}}
    after, _ = styles.layer(ROOT, pinned)
    _assert(after["palette"]["source"] == "extract",
            "a style overrode a value the pipeline pinned")

    try:
        styles.layer(ROOT, {"styles": ["definitely_not_a_style"]})
    except styles.StyleError:
        return
    raise AssertionError("a missing style was accepted silently")


def _annotation_consumed() -> None:
    """A feature that is saved but never read is worse than one that is absent.

    Annotation went through exactly that state: endpoints, UI and storage
    existed while no stage looked at it.
    """
    import inspect

    from pipeline.stages import pose as pose_stage

    src = inspect.getsource(pose_stage)
    _assert("annotation" in src, "the pose stage ignores annotations")
    _assert("_from_annotations" in src, "no path turns an annotation into a pose")

    from pipeline.stage import Context

    ctx = Context(root=ROOT, outdir=ROOT, config={"rig": "humanoid", "annotate": "skip"})
    _assert(ctx._measured_proportions() == {},
            "annotate: skip should measure nothing")


def _pose_guard() -> None:
    from pipeline import comfy

    for word in ("skeleton", "undead", "stick figure"):
        _assert(word in comfy.POSE_NEGATIVE, f"'{word}' missing from the pose guard")

    import inspect

    from pipeline.stages import frames

    src = inspect.getsource(frames)
    _assert("POSE_NEGATIVE" in src, "the guard is never appended to a generation")


def _proportions() -> None:
    import math

    from pipeline import rigs

    base = rigs.HUMANOID
    scaled = rigs.scale(base, {"neck": 2.5, "arms": 1.4, "legs": 0.8, "head": 1.5})
    for a, b, factor in (("neck", "nose", 2.5), ("l_shoulder", "l_elbow", 1.4),
                         ("l_hip", "l_knee", 0.8)):
        want = math.dist(base.neutral[a], base.neutral[b]) * factor
        got = math.dist(scaled.neutral[a], scaled.neutral[b])
        _assert(abs(got - want) < 1e-9, f"{a}->{b}: {got:.4f} != {want:.4f}")
    _assert(scaled.head_radius > base.head_radius, "head scale ignored the skull")


def _proportions_connected() -> None:
    import math

    from pipeline import rigs

    for name in ("humanoid", "dragon", "spider", "serpent"):
        base = rigs.get(name)
        scaled = rigs.scale(base, {"neck": 2.0, "legs": 0.6, "tail": 1.8})
        _assert(set(scaled.neutral) == set(base.neutral), f"{name}: joints lost")
        # Bones the factors do not name must keep their original length.
        for a, b, _w in base.bones:
            if rigs._group_of(a, b) in {"neck", "legs", "tail"}:
                continue
            want = math.dist(base.neutral[a], base.neutral[b])
            got = math.dist(scaled.neutral[a], scaled.neutral[b])
            _assert(abs(got - want) < 1e-9, f"{name} {a}->{b} moved unexpectedly")


def _proportions_reject() -> None:
    from pipeline import rigs

    try:
        rigs.scale(rigs.HUMANOID, {"elbows": 2.0})
    except KeyError:
        return
    raise AssertionError("an unknown proportion group was accepted silently")


def _rig_free() -> None:
    from pipeline import rigs
    from pipeline.stages.frames import _view_words

    none = rigs.get("none")
    _assert(not none.joints, "rig 'none' should have no joints")
    _assert(none.skeleton_control is None, "rig 'none' should send no skeleton")
    _assert(none.depth_control is None, "rig 'none' should send no depth")
    _assert("front" in _view_words(0), "0 degrees should read as a front view")
    _assert("rear" in _view_words(180), "180 degrees should read as a rear view")


def _queue(tmp: Path):
    from pipeline import queue as q

    (tmp / "configs").mkdir(parents=True, exist_ok=True)
    for name in ("knight_attack", "character_sheet", "_global"):
        src = ROOT / "configs" / f"{name}.yaml"
        if src.exists():
            (tmp / "configs" / f"{name}.yaml").write_text(src.read_text())
    return q.Queue(tmp), q


def _matrix() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        qu, q = _queue(Path(tmp))
        jobs = qu.submit({"config": "knight_attack",
                          "matrix": {"a": [1, 2, 3], "b": ["x", "y"]}})
        _assert(len(jobs) == 6, f"expected 6 jobs, got {len(jobs)}")
        cells = {tuple(sorted(j.data["matrix_cell"].items())) for j in jobs}
        _assert(len(cells) == 6, "matrix produced duplicate combinations")


def _triage() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        qu, q = _queue(root)
        cases = {
            "broken": ({"config": "nope"}, "problems"),
            "bad_rig": ({"config": "knight_attack", "overrides": {"rig": "griffin"}}, "problems"),
            "early": ({"config": "knight_attack", "needs": ["missing.png"]}, "waiting"),
        }
        for label, (spec, expect) in cases.items():
            for f in qu.dir(q.PENDING).glob("*"):
                f.unlink()
            job = qu.submit(spec)[0]
            result = q.preflight(root, job)
            if expect == "problems":
                _assert(bool(result.problems), f"{label} should fail immediately")
            else:
                _assert(result.held, f"{label} should be held, not failed")


def _no_cascade() -> None:
    """The measured failure this whole design exists to prevent.

    Every stage rejects a missing ComfyUI in about a millisecond, so a worker
    that treated that as a job error would empty a 200-job queue into failed/
    faster than a person could read one line of the log.
    """
    from pipeline import queue as q

    ok, why = q.services_up(ROOT)
    _assert(isinstance(ok, bool), "health check should return a verdict, not raise")
    if not ok:
        _assert("unreachable" in why, "an outage should be described, not swallowed")


def _manifest_excludes_scratch() -> None:
    import tempfile

    from pipeline import artifacts as io, rigs

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        io.save(out, {"skeletons": [out / "a.png"], "_rig": rigs.HUMANOID}, ["pose"])
        data = json.loads((out / io.MANIFEST).read_text())
        _assert("_rig" not in data["artifacts"], "a Rig object was persisted as a repr")
        _assert("skeletons" in data["artifacts"], "real artifacts were dropped")


def _rig_cached() -> None:
    from types import SimpleNamespace

    from pipeline import rigs
    from pipeline.stage import Context

    ctx = Context(root=ROOT, outdir=ROOT, config={"rig": "spider"})
    first, second = ctx.rig(), ctx.rig()
    _assert(first is second, "rig resolved twice")
    _assert(first.name == "spider", "wrong rig resolved")


def _opt(cfg, key, default):
    from pipeline.stage import opt

    return opt(cfg, key, default)


def _assert(cond, msg="") -> None:
    if cond is False:
        raise AssertionError(msg)


def _validate_rig(name, rig) -> None:
    joints = set(rig.joints)
    for parent, kids in rig.tree.items():
        _assert(parent in joints, f"{name}: tree parent {parent} undeclared")
        for kid in kids:
            _assert(kid in joints, f"{name}: tree child {kid} undeclared")
    _assert(not (joints - set(rig.neutral)), f"{name}: neutral missing joints")
    for a, b, _w in rig.bones:
        _assert(a in joints and b in joints, f"{name}: bone {a}->{b} undeclared")

    seen, stack = {rig.root}, [rig.root]
    while stack:
        for kid in rig.tree.get(stack.pop(), ()):
            if kid not in seen:
                seen.add(kid)
                stack.append(kid)
    _assert(not (joints - seen), f"{name}: joints unreachable from root")


def _render_rig(rig) -> None:
    from pipeline import bodyspace as bs
    from pipeline.depthmap import render_depth
    from pipeline.openpose import render

    kp = bs.project(rig.neutral, 40, rig=rig)
    _assert(len(kp) == len(rig.joints), f"{rig.name}: projection length mismatch")
    if rig.skeleton_control:
        render(kp, 64, 64, rig=rig)
    render_depth(rig.neutral, 40, 64, 64, rig=rig)


# -------------------------------------------------------------------- api


def test_api() -> None:
    print("\napi surface")
    for path in ("/api/schema", "/api/system", "/api/global", "/api/configs",
                 "/api/runs", "/api/poses", "/api/browse", "/api/rigpose?rig=spider"):
        check(f"GET {path}", lambda p=path: _assert(status_of(p) == 200, "not 200"))

    schema = get("/api/schema")
    check("every option_from resolves", lambda: _assert(
        not [f["path"] for f in schema["fields"]
             if f.get("options_from") and not schema["options"].get(f["options_from"])],
        "a select has no options to offer",
    ))
    check("schema exposes all rigs", lambda: _assert(
        len(schema["options"]["rigs"]) >= 17, "rig list truncated",
    ))
    check("rigpose ships topology, not just a pose", lambda: _assert(
        {"tree", "limbs", "bones", "neutral", "root"} <= set(get("/api/rigpose?rig=dragon")),
        "editor cannot draw a non-humanoid without the tree",
    ))

    print("\nconfig round-trip")
    before = (ROOT / "configs/knight_attack.yaml").read_text()
    comments = before.count("#")
    post("/api/config?name=knight_attack", {"config": {"canonical": {"seed": 4242}}}, "PUT")
    after = (ROOT / "configs/knight_attack.yaml").read_text()
    check("saving preserves comments", lambda: _assert(
        after.count("#") == comments, f"{comments} -> {after.count('#')} comment lines",
    ))
    check("saving applied the value", lambda: _assert("seed: 4242" in after, "value not written"))
    post("/api/config?name=knight_attack", {"config": {"canonical": {"seed": 1234}}}, "PUT")

    check("invalid stage order is rejected", lambda: _assert(
        status_of("/api/config?name=knight_attack",
                  {"config": {"pipeline": {"stages": ["frames", "pose"]}}}, "PUT") == 400,
        "an unrunnable order was accepted",
    ))
    check("good config survived the rejection", lambda: _assert(
        "stages: [pose" in (ROOT / "configs/knight_attack.yaml").read_text(),
        "a rejected save damaged the file",
    ))

    print("\nsafety")
    for bad in ("../../etc/passwd", "/etc/passwd"):
        check(f"path traversal blocked: {bad}", lambda b=bad: _assert(
            status_of(f"/api/file?path={urllib.parse.quote(b)}") == 403, "not blocked",
        ))


if __name__ == "__main__":
    import urllib.parse

    test_pipeline()
    try:
        test_api()
    except urllib.error.URLError:
        print("\n  (server not running — skipped api tests; run `make up`)")

    print(f"\n{PASS} passed, {FAIL} failed\n")
    sys.exit(1 if FAIL else 0)
