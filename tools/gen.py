#!/usr/bin/env python3
"""
Drive a running ComfyUI instance to generate pixel-art sprites from the CLI.

Talks to ComfyUI's HTTP API rather than its web UI, so sprite batches are
reproducible and scriptable: same seed + same prompt = same image, and a whole
animation's worth of frames can share one seed to hold the character stable.

Two speed modes:
  normal  25 steps, CFG 7   -- best quality, slower
  --lcm    8 steps, CFG 1.5 -- LCM LoRA, roughly 3x faster, for iterating

Start the server first:
  cd ComfyUI && PYTORCH_ENABLE_MPS_FALLBACK=1 .venv/bin/python main.py \
      --use-pytorch-cross-attention
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

CKPT = "sd_xl_base_1.0.safetensors"
PIXEL_LORA = "pixel-art-xl.safetensors"
LCM_LORA = "lcm-lora-sdxl.safetensors"
VAE = "sdxl_vae_fp16fix.safetensors"

NEGATIVE = (
    "blurry, soft, smooth gradient, antialiased, jpeg artifacts, photo, "
    "realistic, 3d render, watermark, signature, text, extra limbs, "
    "deformed, low contrast, muddy colors"
)


def build_graph(
    prompt: str,
    negative: str,
    width: int,
    height: int,
    seed: int,
    steps: int,
    cfg: float,
    pixel_strength: float,
    lcm: bool,
    prefix: str,
    batch: int = 1,
) -> dict:
    """Assemble a ComfyUI API-format graph.

    Node ids are strings; a link is [source_node_id, output_slot].
    """
    g: dict = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": CKPT},
        },
        "2": {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": PIXEL_LORA,
                "strength_model": pixel_strength,
                "strength_clip": pixel_strength,
                "model": ["1", 0],
                "clip": ["1", 1],
            },
        },
    }

    model_src, clip_src = ["2", 0], ["2", 1]

    if lcm:
        g["3"] = {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": LCM_LORA,
                "strength_model": 1.0,
                "strength_clip": 1.0,
                "model": ["2", 0],
                "clip": ["2", 1],
            },
        }
        model_src, clip_src = ["3", 0], ["3", 1]

    g.update(
        {
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": clip_src},
            },
            "5": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": negative, "clip": clip_src},
            },
            "6": {
                "class_type": "EmptyLatentImage",
                "inputs": {"width": width, "height": height, "batch_size": batch},
            },
            "7": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": steps,
                    "cfg": cfg,
                    # LCM needs its own sampler + sgm_uniform schedule; the
                    # normal path uses dpmpp_2m/karras, the SDXL workhorse.
                    "sampler_name": "lcm" if lcm else "dpmpp_2m",
                    "scheduler": "sgm_uniform" if lcm else "karras",
                    "denoise": 1.0,
                    "model": model_src,
                    "positive": ["4", 0],
                    "negative": ["5", 0],
                    "latent_image": ["6", 0],
                },
            },
            # SDXL's baked VAE overflows in fp16 and yields black or NaN
            # images on some backends; the fp16-fix VAE avoids that.
            "8": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
            "9": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["7", 0], "vae": ["8", 0]},
            },
            "10": {
                "class_type": "SaveImage",
                "inputs": {"images": ["9", 0], "filename_prefix": prefix},
            },
        }
    )
    return g


class Comfy:
    def __init__(self, host: str) -> None:
        self.host = host.rstrip("/")
        self.client_id = str(uuid.uuid4())

    def _get(self, path: str) -> dict:
        with urllib.request.urlopen(f"{self.host}{path}", timeout=30) as r:
            return json.loads(r.read())

    def alive(self) -> bool:
        try:
            self._get("/system_stats")
            return True
        except Exception:
            return False

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
            detail = e.read().decode(errors="replace")
            raise SystemExit(f"ComfyUI rejected the graph ({e.code}):\n{detail}")

    def wait(self, prompt_id: str, timeout: float, poll: float = 1.5) -> list[dict]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            hist = self._get(f"/history/{prompt_id}")
            if prompt_id in hist:
                entry = hist[prompt_id]
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    msgs = status.get("messages", [])
                    raise SystemExit(f"generation failed:\n{json.dumps(msgs, indent=2)}")
                images = []
                for out in entry.get("outputs", {}).values():
                    images.extend(out.get("images", []))
                if images:
                    return images
            time.sleep(poll)
        raise SystemExit(f"timed out after {timeout}s waiting for {prompt_id}")

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


def main() -> int:
    p = argparse.ArgumentParser(
        description="Generate pixel-art sprites via a running ComfyUI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  gen.py 'knight with a sword, idle pose' --lcm\n"
            "  gen.py 'knight, {idle|walk|attack} pose' -n 3 --seed 42\n"
            "  gen.py 'goblin archer' -n 4 --seed 7 -o out/goblin\n"
        ),
    )
    p.add_argument("prompt", help="positive prompt")
    p.add_argument("-n", "--count", type=int, default=1, help="images to generate")
    p.add_argument("--seed", type=int, default=-1, help="-1 for random (default)")
    p.add_argument(
        "--fixed-seed", action="store_true",
        help="reuse the same seed for every image instead of incrementing; "
             "combine with prompt changes to keep a character consistent",
    )
    p.add_argument("-W", "--width", type=int, default=1024)
    p.add_argument("-H", "--height", type=int, default=1024)
    p.add_argument("--steps", type=int, help="override step count")
    p.add_argument("--cfg", type=float, help="override CFG scale")
    p.add_argument(
        "-s", "--strength", type=float, default=1.2,
        help="pixel-art LoRA strength; 1.2 recommended, lower = subtler (default: 1.2)",
    )
    p.add_argument("--lcm", action="store_true", help="fast 8-step LCM mode")
    p.add_argument(
        "-b", "--batch", type=int, default=1,
        help="images per queued prompt; amortises ~35s fixed overhead. "
             "2-4 is safe on 16GB at 1024px (default: 1)",
    )
    p.add_argument("--negative", default=NEGATIVE)
    p.add_argument("-o", "--outdir", type=Path, default=Path("out"))
    p.add_argument("--host", default="http://127.0.0.1:8188")
    p.add_argument("--timeout", type=float, default=900)
    a = p.parse_args()

    comfy = Comfy(a.host)
    if not comfy.alive():
        print(
            f"No ComfyUI at {a.host}. Start it with:\n"
            f"  cd ComfyUI && PYTORCH_ENABLE_MPS_FALLBACK=1 .venv/bin/python main.py "
            f"--use-pytorch-cross-attention",
            file=sys.stderr,
        )
        return 1

    steps = a.steps if a.steps is not None else (8 if a.lcm else 25)
    cfg = a.cfg if a.cfg is not None else (1.5 if a.lcm else 7.0)
    base_seed = a.seed if a.seed >= 0 else int(time.time() * 1000) % (2**31)

    a.outdir.mkdir(parents=True, exist_ok=True)
    print(
        f"mode={'lcm' if a.lcm else 'normal'} steps={steps} cfg={cfg} "
        f"lora={a.strength} size={a.width}x{a.height} seed={base_seed}"
    )

    # Each queued prompt carries ~35s of fixed cost on Apple Silicon (text
    # encode + VAE decode + graph setup) regardless of step count, so pack
    # several images into one prompt rather than queueing them one at a time.
    done = 0
    while done < a.count:
        n = min(a.batch, a.count - done)
        seed = base_seed if a.fixed_seed else base_seed + done
        graph = build_graph(
            a.prompt, a.negative, a.width, a.height, seed, steps, cfg,
            a.strength, a.lcm, prefix=f"sprite_{seed}", batch=n,
        )
        t0 = time.time()
        pid = comfy.queue(graph)
        images = comfy.wait(pid, a.timeout)
        elapsed = time.time() - t0

        for j, meta in enumerate(images):
            dst = a.outdir / f"sprite_{seed}_{j}.png"
            dst.write_bytes(comfy.fetch(meta))
            done += 1
            print(
                f"[{done}/{a.count}] {dst}  seed={seed}+{j}  "
                f"({elapsed:.1f}s for {len(images)} = {elapsed/max(len(images),1):.1f}s each)"
            )

    print(f"\nNext: pixelize them ->\n  tools/pixelize.py {a.outdir}/*.png -f 8 -c 32 --alpha")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
