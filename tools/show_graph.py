"""What the canonical stage hands ComfyUI for a run, without running it.

Usage: tools/show_graph.py out/runs/<run_id>
"""
import json, shutil, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))

import pipeline.generation.comfy as comfy
from pipeline.generation.stage import Context
from pipeline.looks import styles
from pipeline.shared import settings

RUN = Path(sys.argv[1])
UPLOADS, GRAPHS = [], []


class StubClient:
    def upload_image(self, path):
        UPLOADS.append(Path(path))
        return Path(path).name

    def generate(self, graph, timeout=0):
        GRAPHS.append(graph)
        raise SystemExit(0)


comfy.connect = lambda *a, **k: StubClient()

cfg = styles.effective(ROOT, settings.read_yaml(RUN / "config.yaml"))[0]
# A scratch outdir: an inspector must leave nothing in the run it is reading.
SCRATCH = Path(tempfile.mkdtemp(prefix="show_graph_"))
ctx = Context(root=ROOT, outdir=SCRATCH, config=cfg, run_id=RUN.name)
# A run that died before writing artifacts.json still has its pose on disk.
saved = RUN / "artifacts.json"
if saved.exists():
    entries = json.loads(saved.read_text())["artifacts"]["pose_frames"]["value"]
else:
    entries = json.loads((RUN / "00_pose/pose.json").read_text())["entries"]
ctx.artifacts = {
    "skeletons": sorted(RUN.glob("00_pose/skeleton_*.png")),
    "depthmaps": sorted(RUN.glob("01_depth/depth_*.png")),
    "pose_frames": entries,
}

from pipeline.stages.canonical import CanonicalStage
stage = CanonicalStage()
try:
    stage.run(ctx, stage.prepare(ctx))
except SystemExit:
    pass
except Exception as e:
    print("!!", type(e).__name__, e)

print("\n=== IMAGES UPLOADED TO THE CANONICAL GRAPH ===")
for p in UPLOADS:
    try:
        rel = p.relative_to(ROOT)
    except ValueError:
        rel = p
    print("  ", rel)

if GRAPHS:
    nodes = GRAPHS[0]
    print(f"\n=== GRAPH: {len(nodes)} nodes ===")
    for nid, node in sorted(nodes.items(), key=lambda kv: int(kv[0])):
        ct = node["class_type"]
        if ct == "CLIPTextEncode":
            print(f"\n  [{nid}] {ct}")
            print("      text:", json.dumps(node["inputs"]["text"])[:2000])
        elif "IPAdapter" in ct:
            ins = {k: v for k, v in node["inputs"].items()
                   if not isinstance(v, list)}
            print(f"\n  [{nid}] {ct}  {ins}")
        elif "ControlNet" in ct and "Loader" not in ct:
            ins = {k: v for k, v in node["inputs"].items() if not isinstance(v, list)}
            print(f"\n  [{nid}] {ct}  {ins}")
        elif ct == "LoadImage":
            print(f"  [{nid}] LoadImage  {node['inputs'].get('image')}")
        elif "Lora" in ct:
            ins = {k: v for k, v in node["inputs"].items() if not isinstance(v, list)}
            print(f"  [{nid}] {ct}  {ins}")

shutil.rmtree(SCRATCH, ignore_errors=True)
