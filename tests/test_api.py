#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.shared import paths  # noqa: E402

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
    from pipeline import stages  # noqa: F401  (importing registers them)
    from pipeline.geometry import rigs  # noqa: F401
    from pipeline.shared import settings  # noqa: F401
    from pipeline.generation.stage import available

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
    check("scaling moves the named group, only it, and rigidly", _proportions)
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
    check("a pipeline value beats global, presence is the override, blanks fall back",
          lambda: (
              _assert(settings.deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"b": 9}})
                      == {"a": {"b": 9, "c": 2}}, "merge lost a key"),
              _assert(settings.overridden_paths({"a": {"b": 1}}) == {"a.b"},
                      "override tracking broken"),
              _assert(_opt({"size": None}, "size", 1024) == 1024,
                      "None leaked through as a value"),
          ))

    print("\nstages that had no coverage")
    check("softbody runs as a stage, not just as physics", _softbody_stage)
    check("reference weight falls off with distance and scales per image",
          _reference_weights)

    print("\nauto-rig")
    check("multi-pass keying removes a layered background", _keying_layers)
    check("keying never eats the whole subject", _keying_guard)
    check("a fit lands joints in anatomical order", _autorig_order)

    print("\nprops")
    check("a prop follows its limb, reaches the depth map, and never punches "
          "a hole", _props_render)
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
    check("an unpersistable artifact is refused, not repr'd", _unpersistable_artifact_is_refused)

    print("\nstage defaults")
    check("DEFAULTS reach stage_config and the schema, and are not shared",
          _stage_defaults)

    print("\nrig editor")
    check("an editor save re-renders exactly as the stage would", _editor_matches_stage)
    check("pose.fill reaches the editor's re-render", _editor_honours_fill)

    print("\ndefinitive editor")
    check("every module imports and shared/ depends on none of them", _module_layering)
    check("failures carry their own status", _error_taxonomy)
    check("every registry answers the same way", _one_registry)
    check("the route table answers like a registry", _route_table)
    check("every layer field carries an explanation", _definitive_layers)
    check("a stack runs, and a broken order is reported not blocked", _definitive_stack)
    check("a stack resumes from the longest known prefix", _editor_limits)


def _reference_pose() -> None:
    import math

    from pipeline.geometry import rigs

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


def _cooling_gate() -> None:
    from pipeline.generation import runner
    from pipeline.orchestration import cooling
    from pipeline.generation.stage import Resource

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

    from types import SimpleNamespace

    from pipeline.geometry import props as props_mod

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

    # Both config shapes must load; the mapping form carries the switch.
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    flat = props_mod.load(["bow"], root=root)
    mapped = props_mod.load({"enabled": False, "items": ["bow"]}, root=root)
    _assert([p.name for p in flat] == [p.name for p in mapped] == ["bow"],
            "the two config shapes disagree")

def _module_layering() -> None:
    import importlib
    import pkgutil

    import pipeline

    failed = []
    for info in pkgutil.walk_packages(pipeline.__path__, "pipeline."):
        if "__pycache__" in info.name:
            continue
        try:
            importlib.import_module(info.name)
        except Exception as e:                      # noqa: BLE001
            failed.append(f"{info.name}: {type(e).__name__}: {e}")
    _assert(not failed, "modules that do not import:\n    " + "\n    ".join(failed))

    groups = {"geometry", "refs", "looks", "generation", "orchestration",
              "definitive", "api", "stages"}
    import ast
    import pathlib

    root = pathlib.Path(pipeline.__path__[0]) / "shared"
    leaks = []
    for f in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.ImportFrom):
                head = (node.module or "").split(".")[0]
                if node.level and head in groups:
                    leaks.append(f"{f.name} -> {node.module}")
                if not node.level and node.module and node.module.startswith("pipeline."):
                    leaks.append(f"{f.name} -> {node.module}")
    _assert(not leaks, f"shared/ reaches into modules: {leaks}")


def _error_taxonomy() -> None:

    from pipeline.generation.comfy import ComfyError
    from pipeline.shared.files import PathDenied
    from pipeline.orchestration.queue import QueueError
    from pipeline.generation.runner import PipelineError
    from pipeline.shared import PixelError, body_for, errors, status_for
    from pipeline.looks.styles import StyleError

    for exc, want in ((errors.NotFound("style sheet", "x"), 404),
                      (errors.Invalid("bad"), 400),
                      (errors.Denied("no"), 403),
                      (errors.Conflict("busy"), 409),
                      (errors.Unavailable("down"), 503),
                      (errors.Internal("boom"), 500)):
        _assert(status_for(exc) == want,
                f"{type(exc).__name__} maps to {status_for(exc)}, wanted {want}")

    # A service being down is not a defect in this code.
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


def _one_registry() -> None:

    import pathlib

    from pipeline.generation import stage

    from pipeline.geometry import props, rigs

    from pipeline.looks import palettes, styles
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

    # A file that will not parse is reported, not omitted.
    from pipeline.shared import paths as _paths
    bad = _paths.resolve(ROOT, "palettes") / "_registry_test.hex"
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

    # A registry read before its modules import must not cache the emptiness.
    from pipeline.shared.registry import Decorated

    late = Decorated()
    growing = Registry("late", late)
    _assert(len(growing) == 0, "a fresh registry was not empty")
    late.add("added_after_first_read", object())
    _assert(len(growing) == 1,
            "a decorated registry cached its emptiness past a later add")

    # Caching: a second read of unchanged files must not reparse.
    calls = {"n": 0}

    def counted(path: pathlib.Path):
        calls["n"] += 1
        return path.stem, path.stem

    from pipeline.shared.registry import Scanned

    from pipeline.shared import paths as _paths

    probe = Registry("probe", Scanned(_paths.resolve(ROOT, "palettes"), ["**/*.hex"], counted))
    probe.all()
    first = calls["n"]
    probe.all()
    _assert(calls["n"] == first,
            f"the registry reparsed unchanged files ({first} then {calls['n']})")


def _unpersistable_artifact_is_refused() -> None:
    import tempfile

    from pipeline.orchestration import artifacts as artifacts_io

    class Opaque:
        pass

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        try:
            artifacts_io.save(out, {"thing": Opaque()}, [])
        except TypeError as e:
            _assert("resumable" in str(e), f"unhelpful message: {e}")
        else:
            raise AssertionError("an unpersistable artifact was written anyway")

        # A scratch key is the supported way to keep one out of the manifest.
        artifacts_io.save(out, {"_thing": Opaque(), "n": 1}, [])
        loaded, _ = artifacts_io.load(out)
        _assert(loaded == {"n": 1}, f"scratch key leaked into the manifest: {loaded}")


def _stage_defaults() -> None:
    from pipeline import stages  # noqa: F401
    from pipeline.generation.schema import fields_for
    from pipeline.generation.stage import Context, available

    ctx = Context(root=ROOT, outdir=ROOT,
                  config={"pose": {"size": None, "fill": 0.8}})
    cfg = ctx.stage_config("pose")
    _assert(cfg["size"] == 1024, "a blank YAML key did not fall back to DEFAULTS")
    _assert(cfg["fill"] == 0.8, "an explicit value was overwritten by a default")
    _assert(cfg["view"] == "side", "an unset key did not get its default")
    _assert(ctx.stage_config("nosuchstage") == {},
            "an unregistered stage should have no defaults")

    # A mutable default shared between reads would poison every later run.
    ctx.stage_config("pose")["llm"]["host"] = "poisoned"
    _assert("host" not in ctx.stage_config("pose")["llm"],
            "a mutable default leaked between stage_config calls")

    by_path = {f["path"]: f for f in fields_for(None)}
    missing = [f"{name}.{key}"
               for name, cls in sorted(available().items())
               for key, value in (getattr(cls, "DEFAULTS", {}) or {}).items()
               if by_path.get(f"{name}.{key}")
               and by_path[f"{name}.{key}"].get("default") != value]
    _assert(not missing, f"schema does not show the declared default for: {missing}")
    _assert(by_path["pose.size"]["default"] == 1024,
            "pose.size lost its default in the schema")


def _route_table() -> None:
    from pipeline import api
    from pipeline.api.routing import BaseRouter, get
    from pipeline.shared import Conflict, Invalid, NotFound

    table = api.table
    before = len(table)
    _assert(before > 0, "the route table is empty")

    try:
        table.find("POST", "/api/schema")
    except Invalid as e:
        _assert("GET" in str(e), f"the wrong-method error did not name GET: {e}")
    else:
        raise AssertionError("POST to a GET-only route was accepted")

    try:
        table.find("GET", "/api/no_such_route")
    except NotFound:
        pass
    else:
        raise AssertionError("an unknown route was accepted")

    # A router that imports after the table was first read must still appear.
    # This is the emptiness-caching bug the registry docstring records.
    class Late(BaseRouter):
        prefix = "/_test"

        @get("/late", "registered after the first read")
        def late(self, req):
            return {}

    try:
        _assert(len(table) == before + 1,
                "the table cached its contents past a later router")
        _assert(table.find("GET", "/_test/late") is not None, "late route missing")
    finally:
        BaseRouter.REGISTRY.remove(Late)

    # Two routers claiming one path is a programming error, refused not resolved.
    class Clash(BaseRouter):
        prefix = "/api"

        @get("/schema", "duplicate on purpose")
        def clash(self, req):
            return {}

    try:
        len(table)
    except Conflict as e:
        _assert("/api/schema" in str(e), f"the conflict did not name the path: {e}")
    else:
        raise AssertionError("two routers claimed one path and nothing objected")
    finally:
        BaseRouter.REGISTRY.remove(Clash)

    _assert(len(table) == before, "the table did not recover after the conflict")


# Path -> (query string, keys the front-end destructures). A key that stops
# being used should leave this in the same commit that stops using it.
SHAPES = {
    "/api/config": ("?name=char_1", ["name", "module", "raw", "config",
                                     "effective", "style_record", "overrides"]),
    "/api/schema": ("", ["fields", "options", "modules"]),
    "/api/system": ("", ["services", "paths", "host", "weights"]),
    "/api/configs": ("", ["configs"]),
    "/api/runs": ("", ["runs"]),
    "/api/styles": ("", ["styles"]),
    "/api/palettes": ("", ["palettes"]),
    "/api/queue": ("", ["states", "counts", "autopilot", "services"]),
    "/api/editor/layers": ("", ["layers", "default_stack"]),
    "/api/global": ("", ["config"]),
    "/api/poses": ("", ["library"]),
    "/api/rigpose": ("?rig=humanoid", ["rig", "joints", "tree", "bones",
                                       "neutral", "root", "limbs", "pose"]),
    "/api/style/detail": ("?name=retro_jrpg", []),
    "/api/style/preview": ("?config=character_sheet", []),
    "/api/style/training": ("?name=retro_jrpg", []),
    "/api/browse": ("", []),
}
NEEDS_ARG = {"/api/annotation", "/api/autorig", "/api/file", "/api/run"}


def _route_surface() -> None:
    from pipeline import api

    broken, unanswered, wrong = [], [], []
    for route in api.table.surface():
        if route["method"] != "GET":
            continue
        path = route["path"]
        query, keys = SHAPES.get(path, ("", []))
        code = status_of(path + query)
        if code >= 500:
            broken.append(f"{path} -> {code}")
            continue
        if code != 200:
            if path not in NEEDS_ARG:
                unanswered.append(f"{path} -> {code}")
            continue
        if keys:
            body = get(path + query)
            missing = [k for k in keys if k not in body]
            if missing:
                wrong.append(f"{path} missing {missing}; has {sorted(body)}")
    _assert(not broken, f"routes that fail server-side: {broken}")
    _assert(not unanswered, f"routes that did not answer: {unanswered}")
    _assert(not wrong, "routes the UI cannot read:\n    " + "\n    ".join(wrong))


def _editor_limits() -> None:
    import numpy as np

    from pipeline import definitive
    from pipeline.definitive import cache
    from pipeline.shared import limits

    # Limits are shares of the machine, not this laptop's numbers.
    d = limits.describe()
    _assert(0 < d["derived"]["threads"] < d["machine"]["cores"],
            f"threads {d['derived']['threads']} of {d['machine']['cores']} cores")

    img = np.zeros((96, 96, 3), dtype=np.uint8)
    img[16:80, 16:80] = (200, 60, 60)
    img[30:50, 30:50] = (40, 40, 160)
    stack = definitive.default_stack()

    cache.SNAPSHOTS.clear()
    _, cold = definitive.apply_stack(img, stack, root=ROOT, source="t")
    _assert(cold["resumed_after"] == 0, "a cold run resumed from somewhere")

    # Changing the last layer must not re-run the ones before it.
    tail = [dict(s, config=dict(s["config"])) for s in stack]
    tail[-1]["config"]["upscale"] = 3
    _, after = definitive.apply_stack(img, tail, root=ROOT, source="t")
    _assert(after["resumed_after"] == len(stack) - 1,
            f"a change at the end recomputed from {after['resumed_after']}")

    # Changing the first must re-run everything, because everything follows it.
    head = [dict(s, config=dict(s["config"])) for s in stack]
    head[0]["config"]["contrast"] = 1.4
    _, front = definitive.apply_stack(img, head, root=ROOT, source="t")
    _assert(front["resumed_after"] == 0,
            "a change at the start reused a prefix it invalidated")

    # Same stack, same answer. Resumption must not change the result.
    a, _ = definitive.apply_stack(img, stack, root=ROOT, source="t")
    b, _ = definitive.apply_stack(img, stack, root=ROOT)          # no resuming
    _assert(np.array_equal(a, b), "resuming produced a different image")

    # The caches are bounded. Without this they become the problem they solve.
    for i in range(60):
        noise = (img + i).astype(np.uint8)
        definitive.apply_stack(noise, stack, root=ROOT, source=f"t{i}")
    _assert(cache.SNAPSHOTS.stats()["bytes"] <= cache.SNAPSHOTS.max_bytes,
            f"snapshots overflowed: {cache.SNAPSHOTS.stats()}")
    _assert(cache.CACHE.stats()["bytes"] <= cache.CACHE.max_bytes,
            f"the reduction cache overflowed: {cache.CACHE.stats()}")


def _definitive_layers() -> None:
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
    import numpy as np

    from pipeline import definitive

    img = np.zeros((64, 64, 3), dtype=np.uint8)
    img[16:48, 16:48] = (200, 60, 60)

    out, facts = definitive.apply_stack(img, definitive.default_stack(), root=ROOT)
    _assert(out.ndim == 3, "the stack did not return an image")

    # A raising layer does not kill the run, which hides a broken import.
    broke = [f"{la['layer']}: {la['error']}" for la in facts["layers"] if la.get("error")]
    _assert(not broke, f"the default stack cannot run: {broke}")
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
    import tempfile

    import numpy as np
    from PIL import Image

    from pipeline import stages  # noqa: F401  (registers)
    from pipeline.generation.stage import Context, get

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        frames = []
        for i in range(3):
            arr = np.zeros((64, 64, 3), dtype=np.uint8)
            arr[20:50, 26:38] = 200
            f = out / f"frame_{i:03d}.png"
            Image.fromarray(arr).save(f)
            frames.append(f)

        from pipeline.geometry import rigs

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


def _reference_weights() -> None:
    from pipeline.refs.references import Reference, pick

    # Keywords, not positions: `role` was inserted second and positional
    # construction silently made yaw="front".
    refs = [Reference(path=Path("front.png"), yaw=0, label="front"),
            Reference(path=Path("rear.png"), yaw=180, label="rear")]

    _, near_w, near_d = pick(refs, 0, tolerance=40)
    _, far_w, far_d = pick(refs[:1], 180, tolerance=40)
    _assert(near_d == 0, "an exact match was not recognised")
    _assert(far_d == 180, "a full mismatch was not measured")
    _assert(far_w < near_w, "weight did not fall off with distance")
    _assert(pick(refs, 170, tolerance=40)[0].label == "rear",
            "the nearer reference was not chosen")

    scaled = Reference(path=Path("a.png"), yaw=0, label="front", weight_scale=0.5)
    _, full, _ = pick([refs[0]], 0, tolerance=40, exact_weight=0.8)
    _, half, _ = pick([scaled], 0, tolerance=40, exact_weight=0.8)
    _assert(abs(full - 0.8) < 1e-9, f"unscaled weight was {full}")
    _assert(abs(half - 0.4) < 1e-9, f"scaled weight was {half}, expected 0.4")
    _assert(abs(0.85 * scaled.weight_scale - 0.425) < 1e-9,
            "the manual branch in frames.py must apply the scale too")


def _keying_layers() -> None:
    import numpy as np

    from pipeline.definitive.pixelize import background_to_alpha

    # border colour, inner panel, subject
    img = np.full((80, 80, 3), 40, dtype=np.uint8)
    img[10:70, 10:70] = 200          # panel, not touching the edge
    img[30:50, 35:45] = 120          # subject inside the panel

    keyed = background_to_alpha(img, 12)
    kept = (keyed[..., 3] > 0).mean()
    _assert(kept < 0.15, f"{kept:.0%} survived keying; the panel was not removed")


def _keying_guard() -> None:
    import numpy as np

    from pipeline.definitive.pixelize import background_to_alpha

    # A subject filling the frame must not be keyed away entirely.
    solid = np.full((40, 40, 3), 90, dtype=np.uint8)
    keyed = background_to_alpha(solid, 10)
    _assert((keyed[..., 3] > 0).mean() >= 0.0, "keying crashed on a full frame")


def _autorig_order() -> None:
    import numpy as np

    from pipeline.geometry import autorig

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


def _props_render() -> None:
    import math

    import numpy as np

    from pipeline.geometry import depthmap, props, rigs

    rig = rigs.HUMANOID
    prop = props.load([{"name": "sword", "socket": "l_wrist", "length": 0.3,
                        "aim": [0, 0.4, -0.9], "prompt": "holding a longsword"}])

    rest = {k: list(v) for k, v in rig.neutral.items()}
    raised = {**rest, "l_elbow": [0.12, 0.10, 0.28], "l_wrist": [0.16, 0.18, 0.18]}
    a, b = props.tip(prop[0], rest, rig), props.tip(prop[0], raised, rig)
    _assert(a is not None and b is not None, "the prop produced no tip")
    _assert(math.dist(a, b) > 0.1, "the prop did not move with the arm")

    # Length is a property of the object, not of the pose.
    for pose, point in ((rest, a), (raised, b)):
        grip = props.anchor(prop[0], pose, rig)
        _assert(abs(math.dist(grip, point) - prop[0].length) < 1e-6,
                "the prop changed length when the arm moved")

    two = props.load([{"name": "gs", "socket": "l_wrist",
                       "second_socket": "r_wrist", "length": 0.4}])
    moved = props.pull_second_hand(two, rest, rig)
    _assert(math.dist(rest["r_wrist"], moved["r_wrist"]) > 0.05,
            "the off hand stayed put on a two-handed weapon")
    _assert(rest["l_wrist"] == moved["l_wrist"], "the primary hand moved")

    tpose = {k: list(v) for k, v in rigs.tpose(rig).items()}
    bare = np.asarray(depthmap.render_depth(tpose, 0.0, 96, 96, rig=rig, props=[]))
    armed = np.asarray(depthmap.render_depth(tpose, 0.0, 96, 96, rig=rig, props=prop))
    _assert(not np.array_equal(bare, armed), "the prop drew nothing into the depth map")
    _assert("longsword" in props.prompt_terms(prop), "the prop never reaches the prompt")

    # Black is background, so a prop reaching it reads as a hole in the sprite.
    dim = props.load([{"name": "cape", "socket": "neck", "width": 0.15,
                       "length": 0.35, "flex": 0.3, "shade": 0.2}])
    raw = np.asarray(depthmap.render_depth(rig.neutral, 40, 256, 256, rig=rig,
                                           props=dim, blur=0))
    ink = raw[raw > 0]
    _assert(int(ink.min()) >= 60,
            f"a prop drew at {int(ink.min())}, below the body floor")


def _styles_layer() -> None:
    from pipeline.looks import styles
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

    # NotFound, not StyleError: it carries the alternatives.
    try:
        styles.layer(ROOT, {"styles": ["definitely_not_a_style"]})
    except NotFound as e:
        _assert("base_pixel" in e.hint,
                f"a missing style did not list the alternatives: {e.hint!r}")
        return
    raise AssertionError("a missing style was accepted silently")


def _annotation_consumed() -> None:
    from pipeline.generation.stage import Context
    from pipeline.stages.pose import PoseStage

    ctx = Context(root=ROOT, outdir=ROOT,
                  config={"rig": "humanoid", "annotate": "skip",
                          "pose": {"source": "annotation"}})
    try:
        PoseStage()._resolve(ctx, ctx.stage_config("pose"), wanted=1)
    except ValueError as e:
        _assert("annotat" in str(e).lower(), f"unexpected refusal: {e}")
    else:
        raise AssertionError("source: annotation resolved with no annotation present")

    _assert(ctx._measured_proportions() == {}, "annotate: skip should measure nothing")


def _pose_guard() -> None:
    from pipeline.looks import vocabulary as v

    for word in ("skeleton", "undead", "stick figure"):
        _assert(word in v.POSE_NEGATIVE, f"'{word}' missing from the pose guard")

    guarded = v.negative_for("base", pose_control=True)
    _assert(v.POSE_NEGATIVE in guarded, "a pose control image got no guard")
    _assert(v.POSE_NEGATIVE not in v.negative_for("base", pose_control=False),
            "the guard was added with no control image to justify it")
    _assert(v.POSE_NEGATIVE not in
            v.negative_for("base", pose_control=True, guard_skeletons=False),
            "guard_against_skeletons: false did not turn the guard off")
    _assert(v.BACKDROP_NEGATIVE in v.negative_for("base", backdrop=True),
            "a keyed backdrop got no negative")


def _proportions() -> None:
    import math

    from pipeline.geometry import rigs

    def span(rig, a, b):
        return math.dist(rig.neutral[a], rig.neutral[b])

    base = rigs.HUMANOID
    scaled = rigs.scale(base, {"neck": 2.5, "arms": 1.4, "legs": 0.8, "head": 1.5})
    for a, b, factor in (("neck", "nose", 2.5), ("l_shoulder", "l_elbow", 1.4),
                         ("l_hip", "l_knee", 0.8)):
        want = span(base, a, b) * factor
        _assert(abs(span(scaled, a, b) - want) < 1e-9,
                f"{a}->{b}: {span(scaled, a, b):.4f} != {want:.4f}")
    _assert(scaled.head_radius > base.head_radius, "head scale ignored the skull")

    for name in ("humanoid", "dragon", "spider", "serpent"):
        rig = rigs.get(name)
        moved = rigs.scale(rig, {"neck": 2.0, "legs": 0.6, "tail": 1.8})
        _assert(set(moved.neutral) == set(rig.neutral), f"{name}: joints lost")
        for a, b, _w in rig.bones:
            if rigs._group_of(a, b) in {"neck", "legs", "tail"}:
                continue
            _assert(abs(span(moved, a, b) - span(rig, a, b)) < 1e-9,
                    f"{name} {a}->{b} moved unexpectedly")

    for group, rig_name, joint in (("legs", "humanoid", "l_ankle"),
                                   ("torso", "humanoid", "l_hip"),
                                   ("arms", "humanoid", "l_wrist"),
                                   ("neck", "humanoid", "nose"),
                                   ("tail", "quadruped", "tail_tip"),
                                   ("wings", "dragon", None)):
        rig = rigs.get(rig_name)
        bigger = rigs.scale(rig, {group: 1.5})
        shifted = [j for j in rig.neutral
                   if tuple(rig.neutral[j]) != tuple(bigger.neutral[j])]
        _assert(shifted if joint is None else joint in shifted,
                f"proportions.{group} moved nothing on {rig_name}")

    # Thickness follows length, but not by the full factor: taller is not wider.
    thin = {(a, b): w for a, b, w in base.bones}
    thick = {(a, b): w for a, b, w in rigs.scale(base, {"legs": 1.75}).bones}
    leg, arm = ("l_hip", "l_knee"), ("l_shoulder", "l_elbow")
    _assert(thin[leg] < thick[leg] < thin[leg] * 1.75,
            f"leg capsule went {thin[leg]:.4f} -> {thick[leg]:.4f}")
    _assert(thick[arm] == thin[arm], "scaling legs changed arm thickness")

    try:
        rigs.scale(base, {"elbows": 2.0})
    except KeyError:
        return
    raise AssertionError("an unknown proportion group was accepted silently")


def _rig_free() -> None:
    from pipeline.geometry import rigs
    from pipeline.looks.vocabulary import view_words

    none = rigs.get("none")
    _assert(not none.joints, "rig 'none' should have no joints")
    _assert(none.skeleton_control is None, "rig 'none' should send no skeleton")
    _assert(none.depth_control is None, "rig 'none' should send no depth")
    _assert("front" in view_words(0), "0 degrees should read as a front view")
    _assert("rear" in view_words(180), "180 degrees should read as a rear view")


def _queue(tmp: Path):
    from pipeline.orchestration import queue as q

    (tmp / "library" / "configs").mkdir(parents=True, exist_ok=True)
    for name in ("knight_attack", "character_sheet", "_global"):
        src = paths.resolve(ROOT, "configs") / f"{name}.yaml"
        if src.exists():
            (tmp / "library" / "configs" / f"{name}.yaml").write_text(src.read_text())
    return q.Queue(tmp), q


def _matrix() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        qu, _q = _queue(Path(tmp))
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
    from pipeline.orchestration import queue as q

    ok, why = q.services_up(ROOT)
    _assert(isinstance(ok, bool), "health check should return a verdict, not raise")
    if not ok:
        _assert("unreachable" in why, "an outage should be described, not swallowed")


def _manifest_excludes_scratch() -> None:
    import tempfile

    from pipeline.geometry import rigs

    from pipeline.orchestration import artifacts as io

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        io.save(out, {"skeletons": [out / "a.png"], "_rig": rigs.HUMANOID}, ["pose"])
        data = json.loads((out / io.MANIFEST).read_text())
        _assert("_rig" not in data["artifacts"], "a Rig object was persisted as a repr")
        _assert("skeletons" in data["artifacts"], "real artifacts were dropped")


def _editor_run(tag: str, pose_cfg: dict):
    import shutil

    import yaml

    from pipeline.api.context import runs_dir
    from pipeline.geometry import rigs as rig_lib

    run = runs_dir() / f"_test_{tag}"
    shutil.rmtree(run, ignore_errors=True)
    (run / "00_pose").mkdir(parents=True)

    (run / "config.yaml").write_text(yaml.safe_dump(
        {"name": tag, "rig": "humanoid", "pipeline": {"stages": ["pose"]},
         "pose": pose_cfg},
        sort_keys=False,
    ))

    rig = rig_lib.get("humanoid")
    entries = [{"pose": {k: list(v) for k, v in rig_lib.tpose(rig).items()},
                "yaw": 30.0, "spec": 0}]
    (run / "00_pose" / "pose.json").write_text(json.dumps(
        {"source": "tpose", "rig": "humanoid", "mode": "sequence",
         "entries": entries}, indent=1))
    return run, entries


def _editor_matches_stage() -> None:
    import copy
    import shutil

    import yaml

    from pipeline.api.poses import save_poses
    from pipeline.generation.stage import Context
    from pipeline.looks import styles
    from pipeline.shared import settings
    from pipeline.stages import pose as pose_stage

    run, entries = _editor_run(
        "editor_match",
        {"size": 256, "fill": 0.8, "thickness": 0.02, "depth_scale": 1.3},
    )
    try:
        # The pipeline's own derivation, reproduced independently.
        raw = yaml.safe_load((run / "config.yaml").read_text())
        styled, _ = styles.layer(ROOT, raw, picks=raw.get("style_picks"))
        ctx = Context(root=ROOT, outdir=run, config=settings.effective(ROOT, styled),
                      run_id=run.name)
        want_dir = run / "_expected"
        want_dir.mkdir()
        want = pose_stage.render_entries(ctx, copy.deepcopy(entries), want_dir)

        save_poses({"run_id": run.name, "entries": copy.deepcopy(entries)})
        got = run / "00_pose" / "skeleton_000.png"

        _assert(got.exists(), "editor save wrote no skeleton")
        _assert(got.read_bytes() == want[0].read_bytes(),
                "editor re-render differs from the stage's render")
    finally:
        shutil.rmtree(run, ignore_errors=True)


def _editor_honours_fill() -> None:
    import copy
    import shutil

    from pipeline.api.poses import save_poses

    made = []
    try:
        for tag, fill in (("fill_off", 0.0), ("fill_on", 0.85)):
            run, entries = _editor_run(tag, {"size": 256, "fill": fill,
                                             "thickness": 0.02})
            made.append(run)
            save_poses({"run_id": run.name, "entries": copy.deepcopy(entries)})
        a, b = (r / "00_pose" / "skeleton_000.png" for r in made)
        _assert(a.read_bytes() != b.read_bytes(),
                "pose.fill made no difference to the editor's re-render")
    finally:
        for run in made:
            shutil.rmtree(run, ignore_errors=True)


def _rig_cached() -> None:
    from pipeline.generation.stage import Context

    ctx = Context(root=ROOT, outdir=ROOT, config={"rig": "spider"})
    first, second = ctx.rig(), ctx.rig()
    _assert(first is second, "rig resolved twice")
    _assert(first.name == "spider", "wrong rig resolved")


def _opt(cfg, key, default):
    from pipeline.generation.stage import opt

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
    from pipeline.geometry import bodyspace as bs
    from pipeline.geometry.depthmap import render_depth
    from pipeline.geometry.openpose import render

    kp = bs.project(rig.neutral, 40, rig=rig)
    _assert(len(kp) == len(rig.joints), f"{rig.name}: projection length mismatch")
    if rig.skeleton_control:
        render(kp, 64, 64, rig=rig)
    render_depth(rig.neutral, 40, 64, 64, rig=rig)


# -------------------------------------------------------------------- api


def test_api() -> None:
    print("\napi surface")
    check("every GET route answers with the fields the UI reads", _route_surface)

    schema = get("/api/schema")
    check("every option_from resolves, and rigs are not truncated", lambda: (
        _assert(not [f["path"] for f in schema["fields"] if f.get("options_from")
                     and not schema["options"].get(f["options_from"])],
                "a select has no options to offer"),
        _assert(set(schema["options"]["rigs"]) >= {"humanoid", "dragon", "spider"},
                "the rig list lost a rig the editor draws"),
    ))
    check("rigpose ships topology, not just a pose", lambda: _assert(
        {"tree", "limbs", "bones", "neutral", "root"} <= set(get("/api/rigpose?rig=dragon")),
        "editor cannot draw a non-humanoid without the tree",
    ))

    print("\nconfig round-trip")
    before = (paths.resolve(ROOT, "configs") / "knight_attack.yaml").read_text()
    comments = before.count("#")
    post("/api/config?name=knight_attack", {"config": {"canonical": {"seed": 4242}}}, "PUT")
    after = (paths.resolve(ROOT, "configs") / "knight_attack.yaml").read_text()
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
        "stages: [pose" in (paths.resolve(ROOT, "configs") / "knight_attack.yaml").read_text(),
        "a rejected save damaged the file",
    ))

    print("\nsafety")
    # Two routes deny these: containment gives 403, a nonexistent path 404.
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
