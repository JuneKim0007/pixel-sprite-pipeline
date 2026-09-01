from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from pipeline.generation import comfy
from pipeline.generation.stage import Context
from pipeline.geometry import rigs
from pipeline.refs import references as refs_mod


def png(path, size=(8, 8), shade: int = 128):
    """A flat image on disk. Every stage test needs one and none of them care what is in it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (shade, shade, shade)).save(path)
    return path


def png_bytes(shade: int = 200) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (shade, shade, shade)).save(buf, format="PNG")
    return buf.getvalue()


class FakeComfy:
    """Records what a stage asked ComfyUI to do, and answers with flat images.

    The graph a stage hands to `generate` is the whole of what it decided:
    prompt, weights, control channels, seed, steps. Recording it is what makes
    a body that needs a GPU checkable without one.
    """

    def __init__(self, host: str = "") -> None:
        self.host = host
        self.uploads: list[str] = []
        self._uploaded: dict = {}
        self.graphs: list[dict] = []
        self.alive_answer = True
        self.missing_nodes: set[str] = set()

    def alive(self) -> bool:
        return self.alive_answer

    def has_node(self, node: str) -> bool:
        return node not in self.missing_nodes

    def upload_image(self, path, subfolder: str = "pipeline") -> str:
        """Memoised exactly as `comfy.Client` is, so `uploads` counts what the
        network would actually see rather than what the caller asked for."""
        if path in self._uploaded:
            return self._uploaded[path]
        self.uploads.append(Path(path).name)
        self._uploaded[path] = f"{subfolder}/{Path(path).name}"
        return self._uploaded[path]

    def generate(self, graph: dict, timeout: float = 1800) -> list[bytes]:
        self.graphs.append(graph)
        batch = 1
        for node in graph.values():
            if node["class_type"] == "EmptyLatentImage":
                batch = int(node["inputs"].get("batch_size", 1))
        return [png_bytes(200 + i) for i in range(batch)]

    # -- readers the tests use to say what the stage decided

    def classes(self, index: int = -1) -> list[str]:
        return sorted(n["class_type"] for n in self.graphs[index].values())

    def count(self, class_type: str, index: int = -1) -> int:
        return sum(1 for n in self.graphs[index].values()
                   if n["class_type"] == class_type)

    def inputs_of(self, class_type: str, index: int = -1) -> list[dict]:
        return [n["inputs"] for n in self.graphs[index].values()
                if n["class_type"] == class_type]

    def prompt(self, index: int = -1) -> str:
        """The positive prompt: the first CLIPTextEncode a graph declares."""
        texts = [n["inputs"].get("text", "")
                 for nid, n in sorted(self.graphs[index].items(), key=lambda kv: int(kv[0]))
                 if n["class_type"] == "CLIPTextEncode"]
        return texts[0] if texts else ""


@pytest.fixture
def comfy_fake(monkeypatch):
    fake = FakeComfy()
    monkeypatch.setattr(comfy, "Client", lambda host="": fake)
    return fake


@pytest.fixture
def stage_ctx(tmp_path):
    """A Context any stage can run against.

    Cooling is off so a run does not sleep, and the three resources a stage may
    declare are seeded, so no test resolves a rig through an LLM.
    """

    def build(rig=rigs.HUMANOID, **config):
        cfg = {
            "subject": "a knight in armor",
            "style": "pixel art",
            "cooling": {"enabled": False},
        }
        cfg.update(config)
        outdir = tmp_path / "run"
        outdir.mkdir(parents=True, exist_ok=True)
        return Context(
            root=tmp_path, outdir=outdir, config=cfg, run_id="testrun",
            artifacts={},
            resources={"rig": rig,
                       "references": refs_mod.Library(),
                       "rig_record": {"source": "test"}},
        )

    return build


@pytest.fixture(name="png")
def png_fixture():
    """The on-disk image helper, as a fixture so tests need no package import."""
    return png


@pytest.fixture
def pose_entries():
    """Pose entries as PoseStage writes them.

    `posed=False` leaves out the joint positions, which is all the GPU stages
    read and is what the generation tests passed before this was shared.
    """
    def build(n=2, *, step=90.0, rig=None, posed=True):
        entry = ({"pose": rigs.tpose(rig or rigs.HUMANOID)} if posed
                 else {"mode": "library"})
        return [{**entry, "yaw": i * step, "spec": 0} for i in range(n)]
    return build


@pytest.fixture
def frames(tmp_path):
    """n flat frames on disk, named as the stages that write them do."""
    def build(n=2, size=(8, 8)):
        return [png(tmp_path / "in" / f"frame_{i:03d}.png", size) for i in range(n)]
    return build


@pytest.fixture
def skeletons(tmp_path):
    def build(n=1):
        return [png(tmp_path / "in" / f"skeleton_{i:03d}.png") for i in range(n)]
    return build


@pytest.fixture
def frames_ctx(stage_ctx, skeletons, pose_entries, tmp_path):
    """A Context the frames stage can run against: skeletons, poses and an anchor."""
    def build(n=2, **config):
        ctx = stage_ctx(**config)
        ctx.artifacts["skeletons"] = skeletons(n)
        ctx.artifacts["pose_frames"] = pose_entries(n, posed=False)
        ctx.artifacts["canonical"] = png(tmp_path / "in" / "canonical.png")
        return ctx
    return build
