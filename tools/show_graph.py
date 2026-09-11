"""What the canonical stage hands ComfyUI for a run, without running it.

Usage: tools/show_graph.py out/runs/<run_id>
"""
import json, sys
from pathlib import Path
ROOT = Path("/Users/personal_jk/pixel"); sys.path.insert(0, str(ROOT))

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
ctx = Context(root=ROOT, outdir=RUN, config=cfg, run_id=RUN.name)
art = json.loads((RUN / "artifacts.json").read_text())["artifacts"]
ctx.artifacts = {
    "skeletons": sorted(RUN.glob("00_pose/skeleton_*.png")),
    "depthmaps": sorted(RUN.glob("01_depth/depth_*.png")),
    "pose_frames": art["pose_frames"]["value"],
}

from pipeline.stages.canonical import CanonicalStage
stage = CanonicalStage()
try:
    stage.run(ctx, stage.prepare(ctx) if hasattr(stage, "prepare") else {})
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
    g = GRAPHS[0]
    nodes = g if isinstance(g, dict) else g
    print(f"\n=== GRAPH: {len(nodes)} nodes ===")
    for nid, node in sorted(nodes.items(), key=lambda kv: int(kv[0])):
        ct = node["class_type"]
        if ct == "CLIPTextEncode":
            print(f"\n  [{nid}] {ct}")
            print("      text:", json.dumps(node["inputs"]["text"])[:2000])
        elif "IPAdapter" in ct:
            ins = {k: v for k, v in node["inputs"].items()
                   if not isinstance(v, list)}
            masked = [k for k, v in node["inputs"].items()
                      if isinstance(v, list) and k == "attn_mask"]
            print(f"\n  [{nid}] {ct}  {ins}  attn_mask={'yes' if masked else 'NO'}")
        elif "ControlNet" in ct and "Loader" not in ct:
            ins = {k: v for k, v in node["inputs"].items() if not isinstance(v, list)}
            print(f"\n  [{nid}] {ct}  {ins}")
        elif ct == "LoadImage":
            print(f"  [{nid}] LoadImage  {node['inputs'].get('image')}")
        elif "Lora" in ct:
            ins = {k: v for k, v in node["inputs"].items() if not isinstance(v, list)}
            print(f"  [{nid}] {ct}  {ins}")
