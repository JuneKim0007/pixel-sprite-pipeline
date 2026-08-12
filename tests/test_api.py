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
    check("the reference pose is an A-pose, and keeps its bones", _reference_pose)

    print("\nproportions and rig-free")
    check("bone groups scale by their factor", _proportions)
    check("scaling keeps the skeleton connected", _proportions_connected)
    check("unknown proportion groups are rejected", _proportions_reject)
    check("every proportion group actually moves something", _proportions_effective)
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
    check("a gated run does not rest before it stops", _cooling_gate)
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
    check("a per-image weight survives both matching modes", _reference_weight_scale)

    print("\nauto-rig")
    check("multi-pass keying removes a layered background", _keying_layers)
    check("keying never eats the whole subject", _keying_guard)
    check("a fit lands joints in anatomical order", _autorig_order)

    print("\nprops")
    check("a prop follows the limb it is held by", _prop_follows_limb)
    check("two-handed props pull the off hand to the grip", _prop_two_handed)
    check("props never draw darker than the body floor", _prop_floor)
    check("props reach the depth map and the prompt", _prop_wired)
    check("a sheet drops its weapons, an animation keeps them", _prop_module_default)

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

    print("\ndefinitive editor")
    check("shared/ depends on no module", _shared_has_no_module_deps)
    check("failures carry their own status", _error_taxonomy)
    check("every registry answers the same way", _one_registry)
    check("every layer field carries an explanation", _definitive_layers)
    check("a stack runs, and a broken order is reported not blocked", _definitive_stack)


def _reference_pose() -> None:
    """Limbs clear of the torso, and not horizontal.

    Both extremes were measured and both failed. Arms down (4 degrees, the
    rig's neutral) put the hand two hip-widths out — against the body, so the
    silhouette has no gap and neither a person nor a model can see where the
    arm ends. A true T (88 degrees) put a long horizontal element at shoulder
    height, and a prompt naming a sword came back with the blade drawn along
    it. 40 degrees is what every character pipeline settles on.

    The rotation must be rigid. Placing each joint along a ray from the
    shoulder preserves shoulder-to-joint distance and silently rescales the
    forearm, which is how this shipped the first time.
    """
    import math

    from pipeline import rigs

    rig = rigs.HUMANOID
    neutral = {k: list(v) for k, v in rig.neutral.items()}
    pose = rigs.tpose(rig)

    def arm_degrees(p):
        return math.degrees(math.atan2(
            abs(p["l_wrist"][0] - p["l_shoulder"][0]),
            abs(p["l_wrist"][2] - p["l_shoulder"][2]) or 1e-9))

    def clearance(p):
        return abs(p["l_wrist"][0]) / (abs(p["l_hip"][0]) or 1e-9)

    _assert(arm_degrees(neutral) < 15, "the rig's neutral is no longer arms-down")
    _assert(30 <= arm_degrees(pose) <= 55,
            f"reference pose arm is {arm_degrees(pose):.0f} degrees, wanted ~40")
    _assert(clearance(pose) > 2 * clearance(neutral),
            "the A-pose does not clear the torso any better than arms-down")
    _assert(arm_degrees(rigs.tpose(rig, spread=88)) > 80, "spread override ignored")

    # Every bone, every humanoid variant, in both symmetry modes.
    for name in ("humanoid", "humanoid_4arm", "humanoid_6arm", "humanoid_tailed"):
        r = rigs.get(name)
        for symmetric in (False, True):
            p = rigs.tpose(r, symmetric=symmetric)
            for a, b, _w in r.bones:
                if a not in r.neutral or b not in r.neutral:
                    continue
                want = math.dist(r.neutral[a][:3:2], r.neutral[b][:3:2])
                got = math.dist(p[a][:3:2], p[b][:3:2])
                if want > 1e-9:
                    _assert(abs(want - got) < 1e-6,
                            f"{name}: bone {a}->{b} changed length "
                            f"{want:.4f} -> {got:.4f}")


def _proportions_effective() -> None:
    """A named group must change the skeleton, or the knob is a lie.

    `proportions.torso` was a no-op on the humanoid and nobody noticed, because
    a no-op looks exactly like a subtle change. The bone from neck to hip is
    the torso, but `_group_of` returned "neck" for anything with neck at either
    end, so the only bone torso could have scaled was filed elsewhere. It was
    set in a shipped style sheet and measured 5.21 heads with and without.

    This checks each group moves at least one joint on a rig that has it,
    rather than checking one case by hand.
    """
    from pipeline import rigs

    cases = [
        ("humanoid", "legs", "l_ankle"),
        ("humanoid", "torso", "l_hip"),
        ("humanoid", "arms", "l_wrist"),
        ("humanoid", "neck", "nose"),
        ("quadruped", "tail", "tail_tip"),
        ("dragon", "wings", None),
    ]
    for rig_name, group, joint in cases:
        rig = rigs.get(rig_name)
        scaled = rigs.scale(rig, {group: 1.5})
        if joint:
            before, after = rig.neutral[joint], scaled.neutral[joint]
            _assert(before != tuple(after),
                    f"proportions.{group} did not move {joint} on {rig_name}")
        else:
            moved = [j for j in rig.neutral
                     if tuple(rig.neutral[j]) != tuple(scaled.neutral[j])]
            _assert(moved, f"proportions.{group} moved nothing on {rig_name}")

    # And the thickness has to follow, or a lengthened limb goes spindly.
    thick_before = {(a, b): w for a, b, w in rigs.HUMANOID.bones}
    thick_after = {(a, b): w for a, b, w in rigs.scale(rigs.HUMANOID, {"legs": 1.75}).bones}
    leg = ("l_hip", "l_knee")
    arm = ("l_shoulder", "l_elbow")
    _assert(thick_after[leg] > thick_before[leg],
            "a lengthened leg kept its original capsule width")
    _assert(thick_after[leg] / thick_before[leg] < 1.75,
            "thickness scaled with the full factor; a taller figure is not a wider one")
    _assert(thick_after[arm] == thick_before[arm],
            "scaling legs changed arm thickness")


def _cooling_gate() -> None:
    """Rests fall between GPU tasks that will actually run.

    Counting the whole plan made the stage before a gate look like it had work
    after it, so `stop_after: canonical` slept three minutes and then returned.
    Gating exists so you can look at something quickly; a wrong sleep here is
    invisible, because it presents as the machine being slow.
    """
    from pipeline import cooling, runner
    from pipeline.stage import Resource

    stages = runner.build(["pose", "depth", "canonical", "frames", "palette"])
    batches = runner.plan(stages)

    def gpu_after(stop_after):
        executed = batches
        if stop_after:
            for i, b in enumerate(batches):
                if stop_after in [s.name for s in b.stages]:
                    executed = batches[: i + 1]
                    break
        return sum(1 for b in executed
                   if any(s.resource == Resource.GPU for s in b.stages))

    _assert(gpu_after(None) == 2, "expected canonical and frames to be GPU work")
    _assert(gpu_after("canonical") == 1,
            "a run gated at canonical still counted frames as upcoming")
    _assert(cooling.estimate({}, gpu_after("canonical")) == 0,
            "a gated run would rest before returning")
    _assert(cooling.estimate({}, gpu_after(None)) > 0,
            "an ungated two-stage run should rest once")

def _prop_module_default() -> None:
    """The same prop list serves both modules and they want opposite things.

    A sheet is a reference document: a weapon occludes the torso it crosses and
    is a long rigid volume the model traces instead of the limb behind it. An
    attack animation without its weapon is an arm swinging at nothing. So the
    default is per module, which is exactly the kind of default that gets
    inverted by a later edit and never noticed.
    """
    from types import SimpleNamespace

    from pipeline import props as props_mod

    def ctx(config):
        return SimpleNamespace(config=config)

    _assert(not props_mod.wanted(ctx({"module": "character_sheet"})),
            "a character sheet drew its props by default")
    _assert(props_mod.wanted(ctx({"module": "animation"})),
            "an animation dropped its props by default")
    _assert(props_mod.wanted(ctx({"module": "character_sheet",
                                  "props": {"enabled": True}})),
            "props.enabled: true did not override the sheet default")
    _assert(not props_mod.wanted(ctx({"module": "animation",
                                      "props": {"enabled": False}})),
            "props.enabled: false did not override the animation default")

    # Both config shapes have to load, since the mapping form is what carries
    # the switch and it used to fail with "no prop 'enabled' in the library".
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    flat = props_mod.load(["bow"], root=root)
    mapped = props_mod.load({"enabled": False, "items": ["bow"]}, root=root)
    _assert([p.name for p in flat] == [p.name for p in mapped] == ["bow"],
            "the two config shapes disagree")

def _shared_has_no_module_deps() -> None:
    """The membership rule for shared/, enforced rather than remembered.

    "Put general things in shared" is a rule everyone agrees with and nobody
    can apply, because everything looks general from the inside. "Depends on
    nothing" is checkable, so it is the rule.

    This also catches the failure that made the split worth doing: opt() sat in
    stage.py beside a service locator, and a module reading one config key
    imported the stage contract to get it.
    """
    import ast
    import pathlib

    shared = pathlib.Path(__file__).resolve().parent.parent / "pipeline" / "shared"
    offenders = []
    for f in sorted(shared.glob("*.py")):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                # A relative import that leaves shared/ is a dependency on a
                # module. Level 1 with no module is `from . import x`, which
                # stays inside; level 2 or more climbs out.
                if node.level and node.level > 1:
                    offenders.append(f"{f.name}: from {'.' * node.level}{node.module or ''}")
                elif node.level == 1 and node.module and "." in node.module:
                    offenders.append(f"{f.name}: from .{node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("pipeline"):
                        offenders.append(f"{f.name}: import {alias.name}")
    _assert(not offenders, f"shared/ reaches into modules: {offenders}")


def _error_taxonomy() -> None:
    """A ValueError reaching the API is a bug; a PixelError is a message.

    Before this, both came back as 500 with a class name in the body, so a
    person typing nothing into a box got:

        500  {"error": "ValueError: a note needs some text"}

    The status is a property of the exception now, and this checks the two
    halves of that: the named types report themselves, and the builtins still
    in use are translated rather than falling through to 500.

    The count at the end is the migration's progress bar. It is asserted as a
    ceiling rather than a target, so it can only go down.
    """
    import ast
    import collections
    import pathlib

    from pipeline.comfy import ComfyError
    from pipeline.files import PathDenied
    from pipeline.queue import QueueError
    from pipeline.runner import PipelineError
    from pipeline.shared import PixelError, body_for, errors, status_for
    from pipeline.styles import StyleError

    for exc, want in ((errors.NotFound("style sheet", "x"), 404),
                      (errors.Invalid("bad"), 400),
                      (errors.Denied("no"), 403),
                      (errors.Conflict("busy"), 409),
                      (errors.Unavailable("down"), 503),
                      (errors.Internal("boom"), 500)):
        _assert(status_for(exc) == want,
                f"{type(exc).__name__} maps to {status_for(exc)}, wanted {want}")

    # A service being down is not a defect in this code, which is the same
    # judgement the queue makes when it pauses instead of failing jobs.
    _assert(status_for(ComfyError("down")) == 503, "ComfyUI down should be 503")
    _assert(status_for(StyleError("cycle")) == 400, "a bad style sheet is the user's")
    _assert(status_for(QueueError("bad")) == 400, "a malformed job is the user's")
    _assert(status_for(PipelineError("order")) == 400, "a bad stage order is the user's")
    _assert(status_for(PathDenied("outside")) == 403, "a denied path is 403")
    for e in (ComfyError("x"), StyleError("x"), QueueError("x"),
              PipelineError("x"), PathDenied("x")):
        _assert(isinstance(e, PixelError),
                f"{type(e).__name__} is outside the taxonomy")

    # Builtins are translated while raise sites are still being named.
    _assert(status_for(ValueError("x")) == 400, "a ValueError should not be a 500")
    _assert(status_for(FileNotFoundError("x")) == 404, "a missing file is 404")
    _assert(status_for(RuntimeError("x")) == 500, "an unexpected error is 500")
    _assert(body_for(RuntimeError("x"))["error"].startswith("RuntimeError:"),
            "an unnamed failure should show its type, as the signal it is")

    # A NotFound says what else was available, because that is usually the
    # question actually being asked.
    listed = errors.NotFound("style sheet", "nope", available=["a", "b"])
    _assert("a, b" in listed.as_dict().get("hint", ""),
            "NotFound did not report the alternatives")

    root = pathlib.Path(__file__).resolve().parent.parent
    builtins = collections.Counter()
    for f in sorted(root.glob("pipeline/**/*.py")):
        if "__pycache__" in str(f):
            continue
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                name = getattr(node.exc.func, "id", "")
                if name in {"ValueError", "FileNotFoundError", "KeyError",
                            "RuntimeError"}:
                    builtins[name] += 1
    total = sum(builtins.values())
    _assert(total <= 80,
            f"builtin raises in pipeline/ rose to {total}: {dict(builtins)}")


def _one_registry() -> None:
    """Six registries, one set of answers.

    They used to disagree. styles raised on a duplicate name; palettes and
    props let the later file win silently. All three swallowed a malformed
    file, so a typo made a palette vanish rather than complain - which presents
    as "the file I just wrote is not in the list" with nothing anywhere saying
    why.

    This checks the three behaviours that were decided independently six times:
    a missing key names the alternatives, a broken file is reported rather than
    dropped, and the scan is cached until the files change.
    """
    import pathlib

    from pipeline import palettes, props, rigs, stage, styles
    from pipeline.definitive import layers
    from pipeline.shared import NotFound
    from pipeline.shared.registry import Registry

    lookups = [("rig", lambda: rigs.get("no_such_rig")),
               ("stage", lambda: stage.get("no_such_stage")),
               ("layer", lambda: layers.get("no_such_layer")),
               ("palette", lambda: palettes.registry(ROOT).get("no_such_palette")),
               ("prop", lambda: props.registry(ROOT).get("no_such_prop")),
               ("style sheet", lambda: styles.registry(ROOT).get("no_such_style"))]

    for what, call in lookups:
        try:
            call()
        except NotFound as e:
            _assert(what in str(e), f"{what} lookup said {e!r}")
            _assert(e.hint, f"{what} lookup did not name the alternatives")
        else:
            raise AssertionError(f"a missing {what} was accepted silently")

    # A file that will not parse is reported, not omitted. This is the failure
    # that motivated the whole change.
    bad = ROOT / "palettes" / "_registry_test.hex"
    bad.write_text("// name: Broken\nnot a hex value\n")
    try:
        reg = palettes.registry(ROOT)
        listed = [b for b in reg.broken() if b.path == bad]
        _assert(listed, "a malformed palette vanished instead of being reported")
        _assert("colour" in listed[0].why.lower(),
                f"the reason was unhelpful: {listed[0].why!r}")
        _assert("_registry_test" not in reg.all(),
                "a malformed palette was loaded anyway")
    finally:
        bad.unlink()

    # And the cache notices the file went away.
    _assert(not [b for b in palettes.registry(ROOT).broken() if b.path == bad],
            "the registry cache did not notice a deleted file")

    # Caching: a second read of unchanged files must not reparse.
    calls = {"n": 0}

    def counted(path: pathlib.Path):
        calls["n"] += 1
        return path.stem, path.stem

    from pipeline.shared.registry import Scanned

    probe = Registry("probe", Scanned(ROOT / "palettes", ["**/*.hex"], counted))
    probe.all()
    first = calls["n"]
    probe.all()
    _assert(calls["n"] == first,
            f"the registry reparsed unchanged files ({first} then {calls['n']})")


def _definitive_layers() -> None:
    """A field with no help is a control the UI cannot explain.

    BaseField renders a disabled marker rather than omitting the tip, so a gap
    is visible on screen. This makes it visible in CI too, because a gap you
    have to notice is a gap that ships.
    """
    from pipeline import definitive

    missing = [(s["key"], f["key"]) for s in definitive.catalogue()
               for f in s["fields"] if not f["help"].strip()]
    _assert(not missing, f"layer fields without help: {missing}")

    for spec in definitive.catalogue():
        _assert(spec["summary"].strip(), f"layer {spec['key']} has no summary")
        keys = [f["key"] for f in spec["fields"]]
        _assert(len(keys) == len(set(keys)), f"{spec['key']} repeats a field key")
        for f in spec["fields"]:
            if f["kind"] == "select":
                _assert(f["options"],
                        f"{spec['key']}.{f['key']} is a select with no options")


def _definitive_stack() -> None:
    """The order is data, and a questionable one warns rather than blocks.

    Someone deliberately keying before the grid to see what happens is doing
    something legitimate; the stage runner takes the same line with a
    questionable pipeline order.
    """
    import numpy as np

    from pipeline import definitive

    img = np.zeros((64, 64, 3), dtype=np.uint8)
    img[16:48, 16:48] = (200, 60, 60)

    out, facts = definitive.apply_stack(img, definitive.default_stack(), root=ROOT)
    _assert(out.ndim == 3, "the stack did not return an image")
    _assert(not facts["warnings"], f"the default order warns: {facts['warnings']}")
    _assert(facts["measured_block"] >= 1, "grid recorded no measurement")

    def stack(*keys):
        return [{"layer": k, "id": k, "enabled": True, "config": {}} for k in keys]

    _assert(definitive.check_order(stack("palette", "grid")),
            "palette before grid should warn")
    _assert(definitive.check_order(stack("background", "grid")),
            "keying before the grid should warn")
    _assert(not definitive.check_order(stack("grid", "palette", "curves")),
            "curves at the end is a legitimate arrangement")

    # A layer that raises reports against itself instead of blanking the run.
    broken = [{"layer": "palette", "id": "p", "enabled": True,
               "config": {"source": "file", "file": "nope"}}]
    out2, facts2 = definitive.apply_stack(img, broken, root=ROOT)
    _assert(out2.shape == img.shape, "a failing layer changed the image")
    _assert(any(la.get("error") for la in facts2["layers"]),
            "a failing layer did not report an error")


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


def _reference_weight_scale() -> None:
    """A per-image weight must survive both matching modes.

    The manual path used to read the configured Identity weight raw and drop
    the per-image scale that pick() had already multiplied in, so every weight
    slider in the UI did nothing the moment automatic matching was switched
    off. A control that is present and inert is worse than an absent one.
    """
    from pipeline.references import Reference, pick

    plain = Reference(path=Path("a.png"), yaw=0, label="front")
    scaled = Reference(path=Path("a.png"), yaw=0, label="front", weight_scale=0.5)

    _, full, _ = pick([plain], 0, tolerance=40, exact_weight=0.8)
    _, half, _ = pick([scaled], 0, tolerance=40, exact_weight=0.8)
    _assert(abs(full - 0.8) < 1e-9, f"unscaled weight was {full}")
    _assert(abs(half - 0.4) < 1e-9, f"scaled weight was {half}, expected 0.4")

    # And the manual branch, as frames.py computes it.
    manual = 0.85 * scaled.weight_scale
    _assert(abs(manual - 0.425) < 1e-9, "manual path must apply the scale too")


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
    from pipeline.shared import NotFound

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

    # NotFound rather than StyleError: a name that does not exist is the
    # registry's answer for every kind of thing, and it carries the
    # alternatives, which is what someone who mistyped actually needs.
    try:
        styles.layer(ROOT, {"styles": ["definitely_not_a_style"]})
    except NotFound as e:
        _assert("base_pixel" in e.hint,
                f"a missing style did not list the alternatives: {e.hint!r}")
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
    # Assert the property that matters - the file is not served - rather than
    # one status code. Both paths are denied, by different routes: an absolute
    # path fails safe_path's containment check (403), while a relative one
    # resolves to somewhere outside the roots that does not exist, so it dies
    # as a missing file (404) before anything is read. Pinning 403 made this
    # fail while the traversal was in fact blocked and leaking nothing.
    for bad in ("../../etc/passwd", "/etc/passwd"):
        check(f"path traversal blocked: {bad}", lambda b=bad: _assert(
            status_of(f"/api/file?path={urllib.parse.quote(b)}") in (403, 404),
            "not blocked",
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
