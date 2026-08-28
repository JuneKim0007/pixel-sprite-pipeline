from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from pipeline.generation import comfy
from pipeline.generation.stage import Context
from pipeline.geometry import rigs
from pipeline.refs import references as refs_mod


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
        self.graphs: list[dict] = []
        self.alive_answer = True
        self.missing_nodes: set[str] = set()

    def alive(self) -> bool:
        return self.alive_answer

    def has_node(self, node: str) -> bool:
        return node not in self.missing_nodes

    def upload_image(self, path, subfolder: str = "pipeline") -> str:
        self.uploads.append(Path(path).name)
        return f"{subfolder}/{Path(path).name}"

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
def gpu_ctx(tmp_path, root):
    """A Context a GPU stage can run against, with cooling off so it does not sleep."""

    def build(**config):
        cfg = {
            "subject": "a knight in armor",
            "style": "pixel art",
            "cooling": {"enabled": False},
        }
        cfg.update(config)
        outdir = tmp_path / "run"
        outdir.mkdir(parents=True, exist_ok=True)
        return Context(
            root=tmp_path,
            outdir=outdir,
            config=cfg,
            run_id="testrun",
            artifacts={},
            resources={"rig": rigs.HUMANOID, "references": refs_mod.Library()},
        )

    return build
