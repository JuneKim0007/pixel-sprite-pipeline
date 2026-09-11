"""Optional stage — re-render an illustration onto the sprite's own pixel grid."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..generation import comfy
from ..generation.stage import Context, Resource, Stage, register
from ..looks import vocabulary

BLOCK_FLOOR = 2


def framed(image, fill: float):
    """Shrink the subject to `fill` of the frame, padding with its own backdrop.

    The sampler fills whatever canvas it is given - measured three times, a
    guide at 0.815, 0.706 and 0.887 of frame height all came back at 0.999 -
    so margin cannot be asked for at generation. Here it can be handed over:
    the latent already has it, and a low denoise keeps it.
    """
    from PIL import Image

    from ..geometry import framing

    box = framing.measure(image)
    if box is None or fill <= 0:
        return image
    scale = min(1.0, fill / max(box.fill, 1e-6))
    if scale >= 0.995:
        return image

    small = image.resize((max(1, round(image.width * scale)),
                          max(1, round(image.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGB", image.size, box.backdrop)
    canvas.paste(small, ((image.width - small.width) // 2,
                         (image.height - small.height) // 2))
    return canvas


def blocked(source: Path, dst: Path, factor: int,
            fill: float = 0.0) -> tuple[int, int]:
    """Quantise to the sprite grid and back, so the latent carries whole blocks.

    Measured across 24 runs, the model draws a 1.75 to 2.00 pixel block on a
    1024 canvas where a 128 sprite wants 8, and no conditioning moved it. What
    it will not invent it can be handed: sampling from this traces the block
    structure instead of inventing a finer one.
    """
    from PIL import Image

    with Image.open(source) as handle:
        image = framed(handle.convert("RGB"), fill)
    cells = (max(1, image.width // factor), max(1, image.height // factor))
    small = image.resize(cells, Image.BOX)
    small.resize(image.size, Image.NEAREST).save(dst)
    return cells


@register
class PixeliseStage(Stage):
    name = "pixelise"
    resource = Resource.GPU
    gives = frozenset({"pixel_anchor"})
    needs = frozenset({"canonical"})
    DEFAULTS = {"denoise": 0.45, "factor": 8, "timeout": 900, "fill": 0.82}

    def run(self, ctx: Context, prep: Mapping[str, Any]) -> dict[str, Any]:
        cfg = ctx.settings("pixelise")
        source = ctx.require("canonical")
        outdir = ctx.stage_dir("pixelise")

        factor = int(cfg["factor"])
        staged = outdir / "blocked.png"
        cells = blocked(source, staged, factor, float(cfg["fill"]))
        print(f"   {source.name} quantised to {cells[0]}x{cells[1]} cells "
              f"and back, block {factor}")

        canonical = ctx.settings("canonical")
        client = comfy.connect(ctx.settings("comfy.host"))
        subject = ctx.config.get("subject") or vocabulary.DEFAULT_SUBJECT
        style = ctx.config.get("style") or vocabulary.DEFAULT_STYLE
        backdrop = vocabulary.backdrop_colour(ctx.settings("background"))
        prompt = canonical.get("prompt") or vocabulary.prompt_for(
            subject, ctx.need("rig").prompt_hint if "rig" in ctx.resources else "",
            style, backdrop)

        g = comfy.Graph()
        model, pos, neg, vae = comfy.base_graph(
            g,
            prompt=prompt,
            negative=vocabulary.negative_for(canonical["negative"],
                                             backdrop=bool(backdrop)),
            lora_strength=float(canonical["lora_strength"]),
            lcm=bool(canonical["lcm"]),
            models=ctx.settings("models"),
        )
        latent = comfy.encode_image(
            g, comfy.load_image(g, client.upload_image(staged)), vae)
        sampling = comfy.Sampling.from_config(canonical,
                                              denoise=float(cfg["denoise"]))
        comfy.sample_and_save(
            g, model, pos, neg, vae,
            sampling=sampling, batch=1, seed=int(canonical["seed"]),
            prefix=f"{ctx.run_id}_pixelise", latent=latent,
        )
        images = client.generate(g.build(), timeout=int(cfg["timeout"]))
        dst = outdir / "pixel_anchor.png"
        dst.write_bytes(images[0])
        print(f"   pixel anchor -> {dst.relative_to(ctx.root)} "
              f"(denoise {cfg['denoise']})")
        return {"pixel_anchor": dst}
