
from __future__ import annotations


import io
import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from dataclasses import dataclass
from typing import Any
from ..shared.config import opt
from ..shared.errors import Unavailable
from ..shared.settings import DEFAULT_GLOBAL

Link = list

def model_name(models: dict, key: str) -> str:
    return str(models.get(key) or DEFAULT_GLOBAL["models"][key])


class ComfyError(Unavailable):
    """ComfyUI is unreachable, or refused a graph."""


def connect(host: str) -> "Client":
    """A client that has answered, or the one sentence that says it has not."""
    client = Client(host)
    if not client.alive():
        raise ComfyError("ComfyUI is not running — start it with ./start.sh")
    return client


class Client:
    def __init__(self, host: str = "http://127.0.0.1:8188") -> None:
        self.host = host.rstrip("/")
        self.client_id = str(uuid.uuid4())


    def _get(self, path: str, timeout: float = 30) -> Any:
        with urllib.request.urlopen(f"{self.host}{path}", timeout=timeout) as r:
            return json.loads(r.read())

    def alive(self) -> bool:
        try:
            self._get("/system_stats", timeout=5)
            return True
        except Exception:
            return False

    def object_info(self, node: str) -> dict:
        return self._get(f"/object_info/{node}")

    def has_node(self, node: str) -> bool:
        try:
            info = self.object_info(node)
            return bool(info)
        except Exception:
            return False

    def upload_image(self, path: Path, subfolder: str = "pipeline") -> str:
        """POST an image into ComfyUI's input area; returns its LoadImage name."""
        boundary = f"----pixel{uuid.uuid4().hex}"
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        buf = io.BytesIO()

        def part(header: str, body: bytes) -> None:
            buf.write(f"--{boundary}\r\n{header}\r\n\r\n".encode())
            buf.write(body)
            buf.write(b"\r\n")

        part(
            f'Content-Disposition: form-data; name="image"; filename="{path.name}"\r\n'
            f"Content-Type: {mime}",
            path.read_bytes(),
        )
        part('Content-Disposition: form-data; name="subfolder"', subfolder.encode())
        part('Content-Disposition: form-data; name="overwrite"', b"true")
        buf.write(f"--{boundary}--\r\n".encode())

        req = urllib.request.Request(
            f"{self.host}/upload/image",
            data=buf.getvalue(),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            info = json.loads(r.read())
        sub = info.get("subfolder") or ""
        return f"{sub}/{info['name']}" if sub else info["name"]

    def queue(self, graph: dict) -> str:
        body = json.dumps({"prompt": graph, "client_id": self.client_id}).encode()
        req = urllib.request.Request(
            f"{self.host}/prompt", data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())["prompt_id"]
        except urllib.error.HTTPError as e:
            raise ComfyError(
                f"ComfyUI rejected the graph ({e.code}):\n"
                f"{e.read().decode(errors='replace')}"
            ) from e

    def wait(self, prompt_id: str, timeout: float = 1800, poll: float = 1.5) -> list[dict]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            hist = self._get(f"/history/{prompt_id}")
            if prompt_id in hist:
                entry = hist[prompt_id]
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise ComfyError(
                        "generation failed:\n"
                        + json.dumps(status.get("messages", []), indent=2)
                    )
                images = [
                    img
                    for out in entry.get("outputs", {}).values()
                    for img in out.get("images", [])
                ]
                if images:
                    return images
            time.sleep(poll)
        raise ComfyError(f"timed out after {timeout}s waiting for {prompt_id}")

    def fetch(self, image: dict) -> bytes:
        q = urllib.parse.urlencode(
            {
                "filename": image["filename"],
                "subfolder": image.get("subfolder", ""),
                "type": image.get("type", "output"),
            }
        )
        with urllib.request.urlopen(f"{self.host}/view?{q}", timeout=60) as r:
            return r.read()

    def generate(self, graph: dict, timeout: float = 1800) -> list[bytes]:
        return [self.fetch(m) for m in self.wait(self.queue(graph), timeout)]


class Graph:
    """Incrementally assembled API-format graph with auto-numbered nodes."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self._n = 0

    def add(self, class_type: str, **inputs: Any) -> str:
        self._n += 1
        node_id = str(self._n)
        self.nodes[node_id] = {"class_type": class_type, "inputs": inputs}
        return node_id

    def out(self, node_id: str, slot: int = 0) -> Link:
        return [node_id, slot]

    def build(self) -> dict:
        return self.nodes


def base_graph(
    g: Graph,
    *,
    prompt: str,
    negative: str,
    lora_strength: float,
    lcm: bool,
    models: dict | None = None,
) -> tuple[Link, Link, Link, Link]:

    models = models or {}
    ckpt = g.add("CheckpointLoaderSimple",
                 ckpt_name=model_name(models, "checkpoint"))
    lora = g.add(
        "LoraLoader",
        lora_name=model_name(models, "pixel_lora"),
        strength_model=lora_strength,
        strength_clip=lora_strength,
        model=g.out(ckpt, 0),
        clip=g.out(ckpt, 1),
    )
    model, clip = g.out(lora, 0), g.out(lora, 1)

    for key, default_strength in (("style_lora", 0.6), ("character_lora", 0.7)):
        name = models.get(key)
        if not name:
            continue
        node = g.add(
            "LoraLoader",
            lora_name=name,
            strength_model=float(models.get(f"{key}_strength", default_strength)),
            strength_clip=float(models.get(f"{key}_strength", default_strength)),
            model=model, clip=clip,
        )
        model, clip = g.out(node, 0), g.out(node, 1)

    if lcm:
        lcm_node = g.add(
            "LoraLoader",
            lora_name=model_name(models, "lcm_lora"),
            strength_model=1.0, strength_clip=1.0,
            model=model, clip=clip,
        )
        model, clip = g.out(lcm_node, 0), g.out(lcm_node, 1)

    pos = g.add("CLIPTextEncode", text=prompt, clip=clip)
    neg = g.add("CLIPTextEncode", text=negative, clip=clip)
    vae = g.add("VAELoader", vae_name=model_name(models, "vae"))

    return model, g.out(pos, 0), g.out(neg, 0), g.out(vae, 0)


def apply_ipadapter(
    g: Graph, model: Link, reference_image: Link, *, weight: float,
    weight_type: str, start_at: float, end_at: float,
    ipadapter: str | None = None,
) -> Link:
    ip_model = g.add("IPAdapterModelLoader",
                     ipadapter_file=ipadapter or DEFAULT_GLOBAL["models"]["ipadapter"])
    clip_vision = g.add("CLIPVisionLoader", clip_name=DEFAULT_GLOBAL["models"]["clip_vision"])
    node = g.add(
        "IPAdapterAdvanced",
        model=model,
        ipadapter=g.out(ip_model, 0),
        image=reference_image,
        clip_vision=g.out(clip_vision, 0),
        weight=weight,
        weight_type=weight_type,
        combine_embeds="concat",
        start_at=start_at,
        end_at=end_at,
        embeds_scaling="V only",
    )
    return g.out(node, 0)


def apply_controlnet(
    g: Graph, positive: Link, negative: Link, control_image: Link, vae: Link,
    *, strength: float, start_percent: float, end_percent: float,
    union_type: str = "openpose",
    controlnet: str | None = None,
) -> tuple[Link, Link]:

    loader = g.add("ControlNetLoader", control_net_name=controlnet or DEFAULT_GLOBAL["models"]["controlnet"])
    cn = g.out(loader, 0)
    if union_type and union_type != "auto":
        cn = g.out(g.add("SetUnionControlNetType", control_net=cn, type=union_type), 0)

    node = g.add(
        "ControlNetApplyAdvanced",
        positive=positive,
        negative=negative,
        control_net=cn,
        image=control_image,
        strength=strength,
        start_percent=start_percent,
        end_percent=end_percent,
        vae=vae,
    )
    return g.out(node, 0), g.out(node, 1)


def encode_image(g: Graph, image: Link, vae: Link) -> Link:

    return g.out(g.add("VAEEncode", pixels=image, vae=vae), 0)


@dataclass(frozen=True)
class Sampling:
    """How to sample, read once from the config block that declares it."""

    width: int
    height: int
    steps: int
    cfg: float
    lcm: bool
    denoise: float = 1.0
    sampler: str | None = None
    scheduler: str | None = None

    @classmethod
    def from_config(cls, block: dict, *, denoise: float = 1.0) -> "Sampling":
        lcm = bool(block["lcm"])
        return cls(
            width=block["width"], height=block["height"],
            steps=opt(block, "steps", 8 if lcm else 25),
            cfg=opt(block, "cfg", 1.5 if lcm else 7.0),
            lcm=lcm, denoise=denoise,
            sampler=block.get("sampler"), scheduler=block.get("scheduler"),
        )


def sample_and_save(
    g: Graph, model: Link, positive: Link, negative: Link, vae: Link,
    *, sampling: Sampling, batch: int, seed: int, prefix: str,
    latent: Link | None = None,
) -> str:
    if latent is None:
        empty = g.add("EmptyLatentImage", width=sampling.width,
                      height=sampling.height, batch_size=batch)
        latent = g.out(empty, 0)

    sampler = g.add(
        "KSampler",
        seed=seed, steps=sampling.steps, cfg=sampling.cfg,
        sampler_name=sampling.sampler or ("lcm" if sampling.lcm else "dpmpp_2m"),
        scheduler=sampling.scheduler or ("sgm_uniform" if sampling.lcm else "karras"),
        denoise=sampling.denoise,
        model=model, positive=positive, negative=negative, latent_image=latent,
    )
    decode = g.add("VAEDecode", samples=g.out(sampler, 0), vae=vae)
    return g.add("SaveImage", images=g.out(decode, 0), filename_prefix=prefix)
