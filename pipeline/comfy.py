"""ComfyUI HTTP client and graph construction.

Graphs are built by composition rather than from static JSON templates: the
canonical stage and the frames stage share a spine (checkpoint → pixel LoRA →
prompts → sampler → VAE → save) and differ only in which conditioning adapters
get spliced in. Templates would mean duplicating that spine and letting the
copies drift.

Node ids are strings and a link is [node_id, output_slot], which is ComfyUI's
API format — the same structure the web UI posts.
"""

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
from typing import Any

Link = list  # [node_id, slot]

CKPT = "sd_xl_base_1.0.safetensors"
PIXEL_LORA = "pixel-art-xl.safetensors"
LCM_LORA = "lcm-lora-sdxl.safetensors"
VAE = "sdxl_vae_fp16fix.safetensors"
CONTROLNET = "controlnet-union-sdxl-promax.safetensors"
IPADAPTER = "ip-adapter_sdxl_vit-h.safetensors"
CLIP_VISION = "CLIP-ViT-H-14.safetensors"

NEGATIVE = (
    "blurry, soft, smooth gradient, antialiased, jpeg artifacts, photo, "
    "realistic, 3d render, watermark, signature, text, extra limbs, "
    "deformed, low contrast, muddy colors"
)

# An OpenPose control image is a figure drawn out of coloured sticks, and a
# model told to follow it closely will sometimes draw those sticks: the output
# comes back as a bony, undead-looking figure instead of the subject wearing
# armour. These terms name that failure so it can be steered away from, and are
# appended automatically whenever a pose control image is in play.
POSE_NEGATIVE = (
    "skeleton, skull, bones, bony, undead, lich, ribcage, x-ray, anatomical "
    "diagram, stick figure, wireframe, rainbow limbs, mannequin"
)


class ComfyError(RuntimeError):
    pass


class Client:
    def __init__(self, host: str = "http://127.0.0.1:8188") -> None:
        self.host = host.rstrip("/")
        self.client_id = str(uuid.uuid4())

    # ------------------------------------------------------------- plumbing

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
        """Introspect a node's real input signature.

        Custom node packs change their parameter names between versions, so the
        pipeline asks the running server what a node actually accepts instead
        of trusting a signature hardcoded here.
        """
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


# ------------------------------------------------------------ graph building


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
    """Checkpoint + style LoRA + optional LCM + both prompts.

    Returns (model, positive, negative, vae).

    The checkpoint is a setting rather than a constant because it is the single
    biggest lever on style. SDXL base is a generalist trained on photographs
    among everything else, so it does not know anime character construction —
    blocking a mushy figure into pixels does not make it a sprite. An anime
    finetune such as Illustrious is the same architecture, so ControlNet,
    IP-Adapter and the pixel LoRA all keep working across the swap.
    """
    models = models or {}
    ckpt = g.add("CheckpointLoaderSimple",
                 ckpt_name=models.get("checkpoint") or CKPT)
    lora = g.add(
        "LoraLoader",
        lora_name=models.get("pixel_lora") or PIXEL_LORA,
        strength_model=lora_strength,
        strength_clip=lora_strength,
        model=g.out(ckpt, 0),
        clip=g.out(ckpt, 1),
    )
    model, clip = g.out(lora, 0), g.out(lora, 1)

    if lcm:
        lcm_node = g.add(
            "LoraLoader",
            lora_name=LCM_LORA,
            strength_model=1.0, strength_clip=1.0,
            model=model, clip=clip,
        )
        model, clip = g.out(lcm_node, 0), g.out(lcm_node, 1)

    pos = g.add("CLIPTextEncode", text=prompt, clip=clip)
    neg = g.add("CLIPTextEncode", text=negative, clip=clip)
    # SDXL's baked VAE overflows in fp16 and can decode to black on MPS.
    vae = g.add("VAELoader", vae_name=VAE)

    return model, g.out(pos, 0), g.out(neg, 0), g.out(vae, 0)


def apply_ipadapter(
    g: Graph, model: Link, reference_image: Link, *, weight: float,
    weight_type: str, start_at: float, end_at: float,
) -> Link:
    """Splice IP-Adapter in to carry character identity from a reference."""
    ip_model = g.add("IPAdapterModelLoader",
                     ipadapter_file=ipadapter or IPADAPTER)
    clip_vision = g.add("CLIPVisionLoader", clip_name=CLIP_VISION)
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
    """Splice ControlNet in to pin the pose.

    Two settings decide whether this works at all:

    union_type   The Union ControlNet handles ten conditioning types in one
                 model and defaults to guessing ("auto"). Telling it the input
                 is an OpenPose skeleton rather than letting it guess is the
                 difference between the pose being applied and quietly ignored.

    end_percent  What fraction of sampling the control steers for. Holding it
                 to 1.0 pins the pose but flattens the pixel style. But this is
                 a FRACTION, so it interacts with step count: 0.2 of 25 steps is
                 5 steps and works, while 0.2 of 8 LCM steps is 1.6 steps and is
                 far too few to establish a pose.
    """
    loader = g.add("ControlNetLoader", control_net_name=controlnet or CONTROLNET)
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
    """Image -> latent, for img2img.

    Denoising from an encoded reference rather than from noise makes the output
    trace that reference's composition. Below about 0.6 denoise it starts
    reproducing the source rather than reinterpreting it, which is the point
    when you want a sheet that matches art you already have.
    """
    return g.out(g.add("VAEEncode", pixels=image, vae=vae), 0)


def sample_and_save(
    g: Graph, model: Link, positive: Link, negative: Link, vae: Link,
    *, width: int, height: int, batch: int, seed: int, steps: int, cfg: float,
    lcm: bool, denoise: float, prefix: str, latent: Link | None = None,
    sampler: str | None = None, scheduler: str | None = None,
) -> str:
    if latent is None:
        empty = g.add("EmptyLatentImage", width=width, height=height, batch_size=batch)
        latent = g.out(empty, 0)

    sampler = g.add(
        "KSampler",
        seed=seed, steps=steps, cfg=cfg,
        # LCM needs its own sampler and schedule; otherwise dpmpp_2m/karras is
        # the SDXL workhorse. Both are overridable for quality experiments.
        sampler_name=sampler or ("lcm" if lcm else "dpmpp_2m"),
        scheduler=scheduler or ("sgm_uniform" if lcm else "karras"),
        denoise=denoise,
        model=model, positive=positive, negative=negative, latent_image=latent,
    )
    decode = g.add("VAEDecode", samples=g.out(sampler, 0), vae=vae)
    return g.add("SaveImage", images=g.out(decode, 0), filename_prefix=prefix)
