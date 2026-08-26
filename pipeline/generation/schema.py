"""The pipeline settings form: what it holds, and how it is rendered."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..geometry.bodyspace import VIEWS as VIEWS_FOR_UI
from ..shared.contracts import ConfigField


MODULES: dict[str, dict[str, Any]] = {
    "character_sheet": {
        "label": "Character sheet",
        "detail": "one pose, several angles",
        "blurb": "One reference pose seen from several angles. Usually the "
                 "first thing you make, and the input to an animation.",
        "available": True,
    },
    "animation": {
        "label": "Animation",
        "detail": "one action, several frames",
        "blurb": "A sequence of frames of one character performing an action.",
        "available": True,
    },
    "tileset": {
        "label": "Tileset",
        "detail": "terrain, 47-blob",
        "blurb": "Top-down terrain tiles that meet their neighbours without a "
                 "seam. A different constraint from a character: a sprite is "
                 "judged on its silhouette, a tile on its edges.",
        "available": False,
    },
    "object": {
        "label": "Objects",
        "detail": "props, no rig",
        "blurb": "Chests, signposts, trees. Neither a character nor a tile - "
                 "no body plan to pose, but placed on a grid.",
        "available": False,
    },
}


FIELDS: list[ConfigField] = [
    ConfigField(key="rig", label="Creature", kind="select",
     options_from="rigs_with_auto", group="Asset",
     help="Body plan. Decides joint layout and which ControlNet channel the "
             "skeleton can be sent to. Only 'humanoid' has a matching OpenPose "
             "model; every other rig uses scribble + depth, and 'blob' uses "
             "depth alone."),
    ConfigField(modules=["character_sheet"], key="detect.model", label="Vision model", kind="text",
     group="Asset",
     help="Used only when Creature is 'auto'. Must be a vision model in "
             "Ollama, e.g. qwen2.5vl:3b. Classification only — a VLM is a poor "
             "judge of joint positions but a good judge of body plan."),
    ConfigField(modules=["character_sheet"], key="detect.min_confidence", label="Min confidence", kind="float",
     min=0.0, max=1.0, step=0.05, group="Asset",
     help="Below this the detection is discarded and humanoid is used. 0 "
             "accepts any answer."),
    ConfigField(key="styles", label="Style sheets", kind="styles",
     options_from="style_names", group="Asset",
     help="Named looks applied beneath this pipeline. A sheet may set "
             "prompts, palette, sampler values and exemplar images together, "
             "because all of those carry a look. Later sheets win over earlier "
             "ones, and the pipeline wins over all of them."),
    ConfigField(key="annotate", label="Reference annotation", kind="select",
     default="skip",
     options=["skip", "if_present", "require"], group="Asset",
     help="Whether this pipeline uses per-image rig annotations. 'skip' "
             "ignores them, which is the right default for unattended runs. "
             "'if_present' uses any that exist. 'require' holds the job until "
             "every reference has one, for when you intend to mark them by "
             "hand before a long batch."),
    ConfigField(key="name", label="Run name", kind="text", group="Asset",
     help="Used for the output folder name."),
    ConfigField(key="subject", label="Subject", kind="textarea", group="Asset",
     help="The character. Read by every stage that writes a prompt, so it "
             "lives in one place."),
    ConfigField(key="style", label="Style", kind="textarea", group="Asset",
     help="Appended to the subject. 'plain flat background' makes the "
             "background keying in the palette stage much cleaner."),

    ConfigField(key="pipeline.stop_after", label="Pause after stage", kind="select",
     options_from="stage_names", group="Pipeline",
     help="Stop the run after this stage so its output can be reviewed or "
             "edited before the expensive stages consume it. Stopping after "
             "'pose' is what makes the rig editor useful — resume continues "
             "with whatever you saved."),
    ConfigField(key="pipeline.stages", label="Stage order", kind="stages",
     group="Pipeline",
     help="Validated before anything runs: a stage whose inputs aren't "
             "produced earlier is rejected with an explanation. Consecutive "
             "independent CPU stages run in parallel; GPU stages never do."),

    ConfigField(key="pose.source", default='library', label="Pose source", kind="select",
     options=["library", "llm", "tpose", "annotation"], group="Pose",
     help="'library' uses hand-authored poses (best quality). 'llm' drafts "
             "a new action from text and caches it. 'tpose' synthesises the "
             "rig's reference pose — what a character sheet uses. 'annotation' "
             "takes the pose from a reference image you marked up: the joints "
             "you placed become the control image, so the generation "
             "reproduces that composition with your character in it. Mark one "
             "up in the Run tab under 'Annotate reference'."),
    ConfigField(modules=["animation"], key="pose.name", default='idle', label="Library pose", kind="select",
     options_from="poses", group="Pose", when={"pose.source": "library"},
     help="A file in poses/. Regenerate with tools/make_poses.py."),
    ConfigField(modules=["animation"], key="pose.action", label="Action (LLM)", kind="textarea",
     group="Pose", when={"pose.source": "llm"},
     help="Plain description of the motion. Be explicit about the ending "
             "position — 'ending in follow-through' beats 'attacking'."),
    ConfigField(key="pose.symmetric", default=False, label="Symmetric reference pose",
     kind="bool", modules=["character_sheet"], group="Pose",
     help="Off by default, and worth leaving off. A mirrored T-pose puts two "
             "horizontal limbs at shoulder height, and a prompt mentioning a "
             "sword or staff often comes back with blades drawn along them "
             "instead of arms. Arms-down also matches how references are "
             "usually posed."),
    ConfigField(key="pose.view", default='side', label="Viewing angle", kind="select",
     options=["front", "three_quarter_front", "side", "three_quarter_rear",
                 "rear_turned", "rear"],
     free_numeric=True, group="Pose",
     help="Poses are stored in body space and projected here, so one pose "
             "file serves every camera. Accepts a raw angle too (e.g. 160). "
             "Past ~100 degrees the face keypoints drop automatically, which "
             "is how the skeleton tells ControlNet the character faces away."),
    ConfigField(key="pose.frames", label="Frame count", kind="int",
     min=1, max=24, group="Pose",
     help="Blank uses every frame in the pose file."),
    ConfigField(key="pose.fill", default=0.0, label="Frame fill", kind="float",
     min=0.0, max=0.98, step=0.02, group="Pose",
     help="Fraction of the canvas the figure occupies. 0 leaves the pose at "
             "the size the rig was authored at, which is small — the humanoid "
             "spans 68%, so a third of the generation resolution goes to empty "
             "background. 0.88 makes a 128px sprite's character 113 pixels tall "
             "instead of 87, for the same GPU time. The tightest axis decides, "
             "so a wingspan cannot run off the sides."),
    ConfigField(key="pose.size", default=1024, label="Skeleton size", kind="int",
     min=256, max=2048, step=64, group="Pose",
     help="Should match the generation resolution."),
    ConfigField(key="pose.depth_scale", default=1.0, label="Depth exaggeration", kind="float",
     min=0.0, max=2.5, step=0.05, group="Pose",
     help="Scales forward/back motion without re-authoring the pose. Above "
             "1.0 makes a swing reach further."),
    ConfigField(key="pose.lateral_scale", default=1.0, label="Stance width", kind="float",
     min=0.0, max=2.5, step=0.05, group="Pose",
     help="Scales the character's left/right spread."),

    ConfigField(key="depth.near", default=255, label="Nearest brightness", kind="int",
     min=0, max=255, step=5, group="Depth",
     help="Grey value for the part of the body closest to the camera. "
             "255 is the convention the ControlNet was trained on."),
    ConfigField(key="depth.far", default=60, label="Farthest brightness", kind="int",
     min=0, max=255, step=5, group="Depth",
     help="Grey value for the farthest part. Raising it flattens the map, "
             "which weakens the sense of rotation; lowering it past ~40 starts "
             "losing the far limb into the background."),
    ConfigField(key="depth.build", label="Build (bulk)", kind="float",
     min=0.3, max=3.0, step=0.05, group="Depth",
     help="How heavy the creature is, as distinct from how tall. The depth "
             "capsules hang off the bones, and their radius is the only thing "
             "that says a character is broad — proportions make a figure "
             "taller, this makes the same skeleton read as heavyset. A YAML "
             "mapping works too, for a pot-bellied build with thin arms: "
             "{torso: 1.6, arms: 0.9}, using the same group names as "
             "proportions."),
    ConfigField(key="depth.blur", default=6.0, label="Softness", kind="float",
     min=0.0, max=24.0, step=0.5, group="Depth",
     help="Gaussian radius over the rendered limbs. Some blur is wanted: a "
             "hard-edged depth map reads as geometry and the model draws tubes."),

    ConfigField(modules=["animation"], key="pose.llm.model", default="qwen3:4b", label="Model", kind="select",
     options_from="ollama", group="LLM", when={"pose.source": "llm"},
     help="Must be pulled in Ollama. Bigger models write better motion."),
    ConfigField(modules=["animation"], key="pose.llm.temperature", default=0.7, label="Temperature", kind="float",
     min=0.0, max=2.0, step=0.05, group="LLM",
     when={"pose.source": "llm"},
     help="Creativity of the LLM only. This has nothing to do with image "
             "variation — that is CFG and seed."),
    ConfigField(modules=["animation"], key="pose.llm.attempts", default=3, label="Retry attempts", kind="int",
     min=1, max=8, group="LLM", when={"pose.source": "llm"},
     help="Rejected poses are fed back as explicit criticism and retried."),
    ConfigField(modules=["animation"], key="pose.llm.tolerance", default=0.3, label="Anatomy tolerance", kind="float",
     min=0.05, max=1.0, step=0.05, group="LLM",
     when={"pose.source": "llm"},
     help="Allowed bone-length drift after anatomy snapping. Tighter is "
             "stricter; snapping already makes bones near-exact."),
    ConfigField(modules=["animation"], key="pose.llm.cache", default=True, label="Cache accepted poses", kind="bool",
     group="LLM", when={"pose.source": "llm"},
     help="Writes to poses/generated/ so a good sequence is reusable."),

    ConfigField(key="canonical.seed", default=1234, label="Seed", kind="int",
     min=0, max=2147483647, group="Canonical",
     help="PIN THIS. Every frame reuses it; changing it changes the "
             "character entirely."),
    ConfigField(key="canonical.lcm", default=False, label="Fast LCM mode", kind="bool",
     group="Canonical",
     help="Off for the canonical. LCM is ~3x faster but drops prompt "
             "details — it lost 'holding a sword' in testing."),
    ConfigField(key="canonical.steps", label="Steps", kind="int",
     min=1, max=150, group="Canonical",
     help="25 for quality. Below ~8 you pay fixed overhead for worse output."),
    ConfigField(key="canonical.cfg", label="CFG", kind="float",
     min=1.0, max=14.0, step=0.1, group="Canonical",
     help="Prompt adherence. 7 is the SDXL sweet spot; use 1.5 with LCM."),
    ConfigField(key="canonical.lora_strength", default=1.2, label="Pixel LoRA strength",
     kind="float", min=0.0, max=2.0, step=0.05, group="Canonical",
     help="1.2 is the pixel-art-xl author's recommendation."),
    ConfigField(key="canonical.width", default=1024, label="Width", kind="int",
     min=512, max=2048, step=64, group="Canonical",
     help="SDXL is trained at 1024; smaller degrades quality and saves less "
             "time than you'd expect."),
    ConfigField(key="canonical.height", default=1024, label="Height", kind="int",
     min=512, max=2048, step=64, group="Canonical",
     help="Pairs with Width. SDXL is trained at 1024x1024 and the further "
             "the canvas is from square, the more the composition "
             "drifts from what the prompt asked for."),

    ConfigField(key="frames.lcm", default=False, label="Fast LCM mode", kind="bool",
     group="Frames",
     help="On for drafts, off for the final render."),
    ConfigField(key="frames.steps", label="Steps", kind="int",
     min=1, max=150, group="Frames",
     help="Frames are conditioned on the anchor, so they need fewer steps "
             "than it does. With Fast LCM mode on this wants single "
             "digits — LCM is distilled to converge in a handful and "
             "the rest buy nothing. With it off, match canonical.steps."),
    ConfigField(key="frames.cfg", label="CFG", kind="float",
     min=1.0, max=14.0, step=0.1, group="Frames",
     help="1.5 with LCM. Using 7 with LCM burns the image."),
    ConfigField(key="frames.denoise", default=1.0, label="Denoise", kind="float",
     min=0.0, max=1.0, step=0.05, group="Frames",
     help="1.0 generates fresh. Below 1.0 carries over the input latent — "
             "this is the 'how much of the previous step survives' dial."),
    ConfigField(key="frames.guard_against_faces", default=True, label="Guard against faces on rear views",
     kind="bool", group="Pose control",
     help="Add face, eyes, nose to the NEGATIVE on rear and near-rear "
             "frames. Depth renders the head as a capsule - measured, a front "
             "and rear depth map differ ~6% across the head and the two side "
             "maps are identical under mirroring - and the skeleton channel, "
             "which does encode facing, is off for a standing sheet. So "
             "nothing geometric says 'no face' and the model draws one."),
    ConfigField(key="frames.guard_against_skeletons", default=True, label="Guard against tracing",
     kind="bool", group="Pose control",
     help="Appends anti-skeleton terms to the negative prompt whenever a "
             "pose guide is used. Without it the model sometimes draws the "
             "guide itself — measured: a T-pose produced a figure with swords "
             "where its arms should be, and an undead-looking body."),
    ConfigField(key="frames.controlnet.enabled", default=True, label="Use the pose guide",
     kind="bool", group="Pose control",
     help="Off is right for a standing character sheet. Measured: with it "
             "on, legs came back as white shafts with ball joints — the guide "
             "drawn as bones — while depth alone gave armoured legs and boots. "
             "Leave it on for attack, hit and fall, where the pose is the "
             "point."),
    ConfigField(key="frames.controlnet.strength", default=0.75, label="ControlNet strength",
     kind="float", min=0.0, max=2.0, step=0.05, group="Pose control",
     help="How hard the pose guide is enforced. Above ~0.85 the model starts "
             "tracing the guide instead of using it, which is what produces "
             "stick-figure or skeletal output."),
    ConfigField(key="frames.controlnet.end_percent", default=0.55, label="ControlNet end %",
     kind="float", min=0.0, max=1.0, step=0.05, group="Pose control",
     help="Fraction of sampling the guide steers for. 0.55 lands the pose "
             "and leaves the second half for the LoRA to render a character "
             "over it; holding it longer produces a traced stick figure."),
    ConfigField(key="frames.ip_adapter.weight", default=0.85, label="IP-Adapter weight",
     kind="float", min=0.0, max=1.5, step=0.05, group="Identity",
     help="0.3-0.5 style nudge | 0.6 mixed | 0.8-1.0 identity lock. Too "
             "high looks pasted on and fights the pose."),
    ConfigField(key="frames.ip_adapter.weight_type", default="linear", label="Weight type",
     kind="select", group="Identity",
     options=["standard", "prompt is more important", "style transfer",
                 "composition", "style and composition"],
     help="How the reference is blended into conditioning."),

    ConfigField(modules=["animation"], key="softbody.fps", default=12.0, label="Playback fps",
     kind="float", min=1, max=60, step=1, group="Softbody",
     help="The rate the spring simulation assumes when it computes lag."),
    ConfigField(modules=["animation"], key="softbody.loop", default=True, label="Cyclic motion",
     kind="bool", group="Softbody",
     help="Pre-rolls the simulation so frame 0 is not sitting at rest, which "
             "otherwise reads as a hitch every time the loop restarts."),
    ConfigField(key="palette.source", default='extract', label="Palette source", kind="select",
     options=["extract", "file", "llm"], group="Palette",
     help="'extract' derives from the canonical and always matches the art. "
             "'file' reuses a committed palette, keeping separate runs of the "
             "same character on-model. 'llm' chooses one by subject — only "
             "chooses; applying it stays deterministic, which is what stops "
             "frames drifting in colour."),
    ConfigField(key="palette.file", default='', label="Palette file", kind="select",
     options_from="palettes", group="Palette",
     when={"palette.source": "file"},
     help="One of the .hex files under palettes/. Committing one is what "
             "makes colour exact rather than probabilistic: snapping is "
             "deterministic, so two runs of the same character land on "
             "the same entries."),
    ConfigField(key="palette.size", default=12, label="Palette size", kind="int",
     min=2, max=64, group="Palette",
     help="Number of colours the whole animation is quantised to."),
    ConfigField(key="palette.factor", default=8, label="Downscale factor", kind="int",
     min=2, max=16, group="Palette",
     help="8 gives 128px sprites from 1024. Verified as this LoRA's true "
             "pixel grid by an intra-block variance sweep."),
    ConfigField(key="palette.reduce", default='median', label="Block reduction", kind="select",
     options=["median", "salient", "clipped", "mode", "mean"], group="Palette",
     help="median measured 100% structural accuracy against ground truth; "
             "mode only 70% (noise makes every colour unique). 'clipped' means "
             "the block after throwing out pixels further than the tolerance "
             "from its mean, then re-averaging - it rejects a specular outlier "
             "that would drag a plain mean, while still letting the survivors "
             "average rather than picking one of them."),
    ConfigField(key="palette.clip_tolerance", default=32.0, label="Clip tolerance", kind="float",
     min=0.0, max=255.0, step=1.0, group="Palette",
     when={"palette.reduce": "clipped"},
     help="RGB distance from the block mean beyond which a pixel is "
             "discarded before re-averaging. Fixed rather than a multiple of "
             "the block's own spread, so one setting behaves the same on a "
             "flat block and a busy one. Blocks where too little survives fall "
             "back to median, which is what stops thin outlines being eaten."),
    ConfigField(key="palette.alpha_tolerance", default=14, label="Background tolerance",
     kind="int", min=0, max=128, group="Palette",
     help="Edge-connected background within this tolerance becomes "
             "transparent. Enclosed regions are preserved."),
    ConfigField(key="palette.upscale", default=1, label="Frame upscale", kind="int",
     min=1, max=16, group="Palette",
     help="Nearest-neighbour, for viewing."),
    ConfigField(key="palette.workers", label="Worker processes", kind="int",
     min=1, max=16, group="Palette",
     help="Blank uses one per core. Frames are independent."),

    ConfigField(key="background.colour", label="Backdrop colour", kind="text",
     group="Palette",
     help="Named in the prompt and removed by the keyer, so the two agree. "
             "Asking for a 'plain' background gets a lit studio card with a "
             "cast shadow, because that is what the words describe. Magenta "
             "rather than chroma green: green sits close to skin and cloth "
             "tones, and every pixel it bleeds into is one the palette has to "
             "spend an entry on."),
    ConfigField(key="background.enabled", label="Force a backdrop", kind="bool",
     group="Palette",
     help="Off leaves the background to the prompt, which is what you want "
             "if a scene is part of the art."),
    ConfigField(key="palette.match", default='weighted', label="Colour matching", kind="select",
     options=["weighted", "luma", "lab", "rgb"], group="Palette",
     help="How 'nearest colour' is decided when snapping to a fixed "
             "palette. 'weighted' applies luminance weights and costs nothing "
             "over plain RGB. 'luma' matches brightness first and is the one "
             "built for sprites — a sprite reads by its value structure, so "
             "an entry of the wrong lightness collapses the form even when "
             "the hue is right. 'lab' is perceptually uniform and the most "
             "faithful for recolouring, at roughly ten times the cost. 'rgb' "
             "is what earlier outputs used. Irrelevant when the palette is "
             "extracted, since it already came from these pixels."),
    ConfigField(key="palette.dither", default=False, label="Dither", kind="bool", group="Palette",
     help="Trades flat blocks for apparent colour depth. Leave off for the "
             "chunky RPG-Maker idiom; turn on when a small palette has to "
             "carry a gradient."),
    ConfigField(key="export.columns", label="Sheet columns", kind="int",
     min=1, max=32, group="Export",
     help="Blank puts every frame in one row."),
    ConfigField(key="export.scale", default=1, label="Sheet upscale", kind="int",
     min=1, max=16, group="Export",
     help="Nearest-neighbour magnification baked into the written sheet, so "
             "4x is sixteen times the pixels on disk. Leave it at 1 "
             "unless the file has to arrive somewhere that will not "
             "magnify it itself — the editor scales in the browser and "
             "stays sharp at any zoom."),

    ConfigField(key="comfy.host", label="ComfyUI host", kind="text",
     default="http://127.0.0.1:8188",
     group="Services",
     help="Where ComfyUI is listening, normally http://127.0.0.1:8188. Set "
             "it here only to point one config at a different instance "
             "than the machine default in _global.yaml."),
    ConfigField(modules=["animation"], key="pose.llm.host", default="http://127.0.0.1:11434", label="Ollama host", kind="text",
     group="Services",
     help="Where Ollama is listening, normally http://127.0.0.1:11434. Only "
             "reached by the settings set to 'llm'; every other path is "
             "deterministic and runs with it down."),

    ConfigField(key="references.from_run", label="Inherit from run", kind="text",
     group="References",
     help="A previous run whose output becomes this run's identity and "
             "palette. How a character sheet feeds an animation without "
             "regenerating the character."),
    ConfigField(key="frames.style_weight", label="Style exemplar strength",
     kind="float", min=0.0, max=0.6, step=0.05, group="References",
     help="How hard style references push. Deliberately an order of "
             "magnitude below identity — at identity strength a style exemplar "
             "replaces your character with the exemplar."),
    ConfigField(key="references.match.tolerance_degrees", default=40.0, label="Match tolerance",
     kind="float", min=0.0, max=180.0, step=5.0, group="References",
     help="A reference within this many degrees of the frame's viewing "
             "angle counts as a match and gets full weight."),
    ConfigField(key="references.pattern", label="Reference path pattern", kind="text",
     group="References",
     help="Find per-view references by filename instead of listing them, so "
             "a job can be written by a script and queued in bulk with nothing "
             "uploaded. Template over {name} (or {id}) and {view}, e.g. "
             "overnight/{name}/refs/{view}.png. Views: front, back, side_left, "
             "side_right. Only files that exist are added, and a view you "
             "configured explicitly always wins."),
    ConfigField(key="references.match.side_fallback", default="none", label="Missing side",
     kind="select", options=["none", "mirror", "back", "front"],
     group="References",
     help="What covers a side with no reference of its own. 'mirror' reuses "
             "the other side flipped - right for a symmetric outfit, wrong for "
             "a cape or an asymmetric skirt. 'back' shares most silhouette "
             "with a profile. 'none' leaves pick() to weaken the nearest "
             "reference, which is the honest default when nothing is known."),
    ConfigField(key="references.match.exact_weight", label="Weight when matched",
     kind="float", min=0.0, max=1.5, step=0.05, group="References",
     help="IP-Adapter weight when a reference matches the view. High is "
             "correct here — you have real evidence of what that side looks "
             "like."),
    ConfigField(key="references.match.far_weight", default=0.45, label="Weight when unmatched",
     kind="float", min=0.0, max=1.5, step=0.05, group="References",
     help="Weight at a full 180-degree mismatch, interpolated in between. "
             "Keep this LOW. Forcing a front reference onto a rear generation "
             "produces a front-facing sprite that fights the pose; a weak hint "
             "leaves the model free to invent the unseen side."),
    ConfigField(key="references.match.auto", default=True, label="Automatic falloff", kind="bool",
     group="References",
     help="On: weight is chosen per frame from angular distance. Off: the "
             "fixed Identity weight is used for every frame."),


    ConfigField(key="canonical.sampler", label="Sampler", kind="select",
     options=['dpmpp_2m', 'dpmpp_2m_sde', 'dpmpp_3m_sde', 'euler', 'euler_ancestral', 'heun', 'dpm_2', 'ddim', 'uni_pc', 'lcm'], group="Quality",
     help="dpmpp_2m is the SDXL workhorse. dpmpp_3m_sde and uni_pc cost "
             "more per step and can resolve finer detail. Must be 'lcm' when "
             "LCM mode is on."),
    ConfigField(key="canonical.scheduler", label="Scheduler", kind="select",
     options=['karras', 'normal', 'simple', 'sgm_uniform', 'exponential', 'beta'], group="Quality",
     help="karras concentrates steps where they matter most. sgm_uniform is "
             "required by LCM."),
    ConfigField(key="frames.sampler", label="Sampler (frames)", kind="select",
     options=['dpmpp_2m', 'dpmpp_2m_sde', 'dpmpp_3m_sde', 'euler', 'euler_ancestral', 'heun', 'dpm_2', 'ddim', 'uni_pc', 'lcm'], group="Quality",
     help="Must be 'lcm' when Fast LCM mode is on — LCM is a distilled model "
             "and an ordinary sampler will not converge inside its step "
             "budget. Off LCM, dpmpp_2m as on the anchor."),
    ConfigField(key="frames.scheduler", label="Scheduler (frames)", kind="select",
     options=['karras', 'normal', 'simple', 'sgm_uniform', 'exponential', 'beta'], group="Quality",
     help="sgm_uniform under LCM, karras otherwise, matching the anchor. The "
             "scheduler decides where the steps are spent, and LCM's "
             "distillation assumes the uniform spacing."),
    ConfigField(key="canonical.controlnet.enabled", default=True, label="Condition the anchor",
     kind="bool", group="Canonical",
     help="Send the pose guide and depth map to the anchor as well as to "
             "the frames. Without it the anchor is generated from prompt and "
             "reference alone, which is how a bow ended up drawn twice: the "
             "words said 'holding a bow' and nothing said where. With it too "
             "strong the anchor traces the depth silhouette instead. The "
             "strengths below default lower than the frames stage's because "
             "the anchor has no anchor of its own to pull against."),
    ConfigField(key="canonical.controlnet.strength", label="Anchor pose strength",
     kind="float", min=0.0, max=1.5, step=0.05, group="Canonical",
     help="0 disables the pose channel without disabling depth."),
    ConfigField(key="canonical.controlnet.end_percent", label="Anchor control end",
     kind="float", min=0.0, max=1.0, step=0.05, group="Canonical",
     help="Fraction of sampling the control steers for. Held late, the "
             "model draws the guide rather than a character."),
    ConfigField(key="canonical.timeout", default=1800, label="Timeout (seconds)", kind="int",
     min=60, max=21600, step=60, group="Canonical",
     help="How long one image may take before the run gives up. Per image, "
             "not per stage — candidates are generated sequentially, so four "
             "of them get four separate budgets."),
    ConfigField(key="frames.timeout", default=1800, label="Timeout (seconds)", kind="int",
     min=60, max=21600, step=60, group="Frames",
     help="Per frame. A stage that hangs holds the queue, so this is the "
             "difference between one bad job and a wasted night."),
    ConfigField(key="canonical.candidates", default=1, label="Canonical candidates", kind="int",
     min=1, max=8, group="Quality",
     help="Generate this many canonical sprites in one batch and keep them "
             "all, so you can pick the best identity anchor instead of "
             "re-rolling. Batching amortises the ~35s fixed cost per prompt."),

    ConfigField(key="proportions.head", label="Head size", kind="float",
     min=0.3, max=3.0, step=0.05, group="Proportions",
     help="Also grows the skull in the depth map, which is where head volume actually reads."),
    ConfigField(key="proportions.neck", label="Neck length", kind="float",
     min=0.3, max=3.0, step=0.05, group="Proportions",
     help="1.0 is the rig's default. 2.0 gives a giraffe-like or serpentine neck."),
    ConfigField(key="proportions.torso", label="Torso length", kind="float",
     min=0.3, max=3.0, step=0.05, group="Proportions",
     help="Stretches the spine; the limbs follow their anchors."),
    ConfigField(key="proportions.arms", label="Arm length", kind="float",
     min=0.3, max=3.0, step=0.05, group="Proportions",
     help="Long-armed apes, short-armed brawlers."),
    ConfigField(key="proportions.legs", label="Leg length", kind="float",
     min=0.3, max=3.0, step=0.05, group="Proportions",
     help="Below 1.0 gives a stubby, chibi stance."),
    ConfigField(key="proportions.tail", label="Tail length", kind="float",
     min=0.3, max=3.0, step=0.05, group="Proportions",
     help="Ignored by rigs with no tail."),
    ConfigField(key="proportions.wings", label="Wing span", kind="float",
     min=0.3, max=3.0, step=0.05, group="Proportions",
     help="Ignored by rigs with no wings."),
    ConfigField(key="proportions.tentacles", label="Tentacle length", kind="float",
     min=0.3, max=3.0, step=0.05, group="Proportions",
     help="Octopoid rigs only."),
    ConfigField(key="proportions.segments", label="Segment length", kind="float",
     min=0.3, max=3.0, step=0.05, group="Proportions",
     help="Serpent and centipede rigs only."),

    ConfigField(key="props_enabled", label="Draw held objects", kind="bool",
     group="Props",
     help="Off by default for a character sheet, on for an animation, and "
             "the same prop list serves both. A sheet is a reference document "
             "- a weapon occludes the torso and arm it crosses, and is a long "
             "rigid volume the model will trace instead of the limb behind it. "
             "An attack animation is the opposite: without the weapon the arm "
             "swings at nothing."),

    ConfigField(key="models.checkpoint", label="Checkpoint", kind="select",
     options_from="checkpoints", group="Models",
     help="Base diffusion weights. SDXL is what the pixel LoRA was trained "
             "against; swapping the base usually means swapping the LoRA too."),
    ConfigField(key="models.pixel_lora", label="Style LoRA", kind="select",
     options_from="loras", group="Models",
     help="The LoRA that makes the output read as pixel art rather than a "
             "downscaled painting. Every other default here is tuned "
             "around it, so swapping it means re-tuning both "
             "lora_strength dials."),
    ConfigField(key="frames.ip_adapter.anchor", default=True, label="Anchor to canonical",
     kind="bool", group="Identity",
     help="Apply the canonical sprite to every frame at equal weight, "
             "underneath the matched identity reference. It is the only input "
             "that does not vary, so it is what keeps frames on-model with "
             "each other. Turning it off reverts to steering from the "
             "reference alone, which leaves rear views with no anchor."),
    ConfigField(key="frames.ip_adapter.anchor_weight", default=0.9, label="Anchor weight",
     kind="float", min=0.0, max=1.2, step=0.05, group="Identity",
     help="How hard the canonical pulls. Too high and every frame becomes "
             "the canonical's pose; too low and the frames drift apart."),
    ConfigField(key="frames.ip_adapter.anchor_weight_type", default="linear", label="Anchor transfer",
     kind="select",
     options=["linear", "style transfer", "style and composition", "strong style transfer"],
     group="Identity",
     help="What the anchor carries. Keep 'linear'. 'style and composition' "
             "also copies the canonical's LAYOUT, and the canonical is "
             "front-facing — at anchor weight 0.9 it outvoted the depth map "
             "that carries yaw and every side and rear frame came back facing "
             "front, with the canonical's backdrop over the keyed one."),
    ConfigField(key="frames.ip_adapter.anchor_falloff", default=0.0, label="Anchor falloff",
     kind="float", min=0.0, max=1.0, step=0.05, group="Identity",
     help="How much the anchor weakens as a frame turns away from it. "
             "Reference weight already falls off with angle; the anchor's did "
             "not, so on a rear frame the FRONT canonical stayed at full "
             "weight while the rear reference was discounted for being far "
             "away — the anchor outvoted the only image showing the back. "
             "0 keeps the old fixed behaviour. Needs a reference at the far "
             "views, or nothing holds them together."),
    ConfigField(key="frames.ip_adapter.anchor_far_weight", default=0.5, label="Anchor weight when opposite",
     kind="float", min=0.0, max=1.2, step=0.05, group="Identity",
     help="What the anchor decays to 180 degrees away, at falloff 1.0."),
    ConfigField(key="frames.ip_adapter.anchor_end_at", default=1.0, label="Anchor end %",
     kind="float", min=0.0, max=1.0, step=0.05, group="Identity",
     help="Fraction of sampling the anchor steers for. 1.0 holds identity "
             "throughout. Lower it if frames still inherit the anchor's pose "
             "after switching transfer to linear."),
    ConfigField(key="models.style_lora", label="Style LoRA", kind="select",
     options_from="loras", group="Models",
     help="A look you trained, stacked on top of the pixel LoRA. One style "
             "LoRA serves every character, which is why it is the better first "
             "thing to train. Watch the total: pixel-art-xl already runs at "
             "1.2, so two style signals compete unless one comes down."),
    ConfigField(key="models.style_lora_strength", label="Style LoRA strength",
     kind="float", min=0.0, max=1.5, step=0.05, group="Models",
     help="How hard the second style signal pulls. pixel-art-xl already runs "
             "at 1.2, so this competes with it: if the look goes muddy, "
             "bring one of the two down rather than pushing this up."),
    ConfigField(key="models.character_lora", label="Character LoRA", kind="select",
     options_from="loras", group="Models",
     help="One character, trained across ALL its views. A LoRA cancels what "
             "varies, so pooling the angles teaches it who the character is "
             "and discards the camera - which is right, because depth and "
             "ControlNet supply the angle. Split per view it would bake the "
             "camera into identity and fight them."),
    ConfigField(key="models.character_lora_strength", label="Character LoRA strength",
     kind="float", min=0.0, max=1.5, step=0.05, group="Models",
     help="Start low (0.4-0.6) and raise. Strength at inference is a "
             "reversible dial; an under-trained LoRA is not."),
    ConfigField(key="models.vae", label="VAE", kind="select",
     options_from="vae", group="Models",
     help="Must match the checkpoint's family. A mismatched VAE does not "
             "error — it decodes to a colour cast with crushed blacks, which "
             "is what an Illustrious A/B produced while borrowing SDXL's."),
    ConfigField(key="models.controlnet", label="ControlNet", kind="select",
     options_from="controlnets", group="Models",
     help="The Union ProMax model covers openpose, depth, scribble and more "
             "in one file, which keeps memory down versus one model per type."),
    ConfigField(key="models.ipadapter", label="IP-Adapter", kind="select",
     options_from="ipadapters", group="Models",
     help="The IP-Adapter weights that carry identity from the reference "
             "into each frame. It has to match the checkpoint's "
             "architecture; an SD1.5 adapter cannot condition an SDXL "
             "base."),

    ConfigField(key="cooling.enabled", label="Rest between GPU tasks", kind="bool",
     group="Compute",
     help="Pause after each GPU task so a long queue does not hold the "
             "machine at its throttle point all night. Nothing needs the pause "
             "to work — it is there because sustained heat shortens a battery "
             "and a throttling machine finishes anyway, quietly aged."),
    ConfigField(key="cooling.seconds", label="Rest length (seconds)", kind="int",
     min=0, max=1800, step=30, group="Compute",
     help="Per rest, and rests fall between tasks — between stages, between "
             "candidates, between frames — never before the first or after the "
             "last. At 180s a fifty-image night spends two and a half hours "
             "resting; that is the trade being made."),
    ConfigField(key="compute.vram_mode", label="VRAM policy", kind="select",
     options=["--gpu-only", "--highvram", "--normalvram", "--lowvram",
              "--novram", "--cpu"],
     default="--lowvram", group="Compute",
     help="Spliced straight into ComfyUI's argv at launch, so it is the flag "
             "and not the word: '--gpu-only' keeps text encoders on the GPU "
             "(~8% faster here), '--lowvram' offloads aggressively — slower, "
             "but survives bigger models. Requires a restart."),
    ConfigField(key="compute.mps_high_watermark", label="MPS memory ceiling",
     kind="float", min=0.0, max=1.0, step=0.05, group="Compute",
     help="Fraction of unified memory PyTorch may allocate "
             "(PYTORCH_MPS_HIGH_WATERMARK_RATIO). 0.0 disables the limit. "
             "Lower it to leave room for other apps; too low causes OOM."),
    ConfigField(key="compute.torch_threads", label="CPU threads (torch)",
     kind="int", min=1, max=16, group="Compute",
     help="Threads for CPU-side tensor work. The M4 has 4 performance and "
             "6 efficiency cores; more threads is not always faster."),
    ConfigField(key="compute.cpu_workers", label="CPU worker processes",
     kind="int", min=1, max=16, group="Compute",
     help="Parallel processes for CPU stages such as pixelization. "
             "Overrides palette.workers when set."),
    ConfigField(key="compute.batch", label="Batch size", kind="int",
     min=1, max=8, group="Compute",
     help="Images per queued prompt. Each prompt costs ~35s of fixed "
             "overhead regardless of steps, so batching amortises it: batch 4 "
             "measured 49s/image versus 71s one at a time."),

    ConfigField(key="canonical.per_view", default=False, label="One anchor per view", kind="bool",
     group="Canonical",
     help="Generate a canonical for every view in pose.set instead of one "
             "for the first. With a single front anchor the other views must "
             "invent their angle - the anchor is the right medium facing the "
             "wrong way, the matched reference the right way in the wrong "
             "medium. Per view one input is correct on both. They do not drift "
             "apart, because each is pinned to a labelled view of the same "
             "reference sheet. Costs one generation per view."),
    ConfigField(key="canonical.batch_candidates", default=True, label="Batch the candidates",
     kind="bool", group="Canonical",
     help="One prompt for all candidates instead of one each. Measured 14% "
             "faster and under half the swapping — but batching cannot rest "
             "between candidates, so an overnight run with cooling on wants "
             "this off, and a 1280 canvas may not fit the batch in 16 GB."),
    ConfigField(key="canonical.style_weight", label="Style exemplar strength",
     kind="float", min=0.0, max=0.6, step=0.05, group="Canonical",
     help="Exemplars carry rendering, not colour. At the 0.35 role default "
             "silver-haired exemplars pulled a crimson character toward "
             "lavender; 0.18 keeps the brushwork and lets the prompt keep the "
             "palette. frames.style_weight was declared and this was not."),
    ConfigField(key="canonical.view", label="Anchor view", kind="text",
     group="Canonical",
     help="Which way the anchor faces — a named view or degrees. Blank "
             "follows the first entry in pose.set, which is what a sheet "
             "wants: every other frame then turns away from it symmetrically."),
    ConfigField(key="canonical.negative", default='blurry, soft, smooth gradient, antialiased, jpeg artifacts, photo, realistic, 3d render, watermark, signature, text, extra limbs, deformed, low contrast, muddy colors', label="Negative prompt", kind="textarea",
     group="Canonical",
     help="Naming a failure is what stops it. The default names the "
             "tracing failure (skeleton, bones, stick figure, wireframe)."),
    ConfigField(key="canonical.controlnet.union_type", label="Anchor control type",
     kind="text", group="Canonical",
     help="The Union model handles ten conditioning types and defaults to "
             "guessing. Naming the input is the difference between the pose "
             "being applied and quietly ignored."),
    ConfigField(key="canonical.controlnet.start_percent", default=0.0, label="Anchor control start",
     kind="float", min=0.0, max=1.0, step=0.05, group="Canonical",
     help="Fraction of sampling before the control begins. At 0 the "
             "composition is fixed from the first step; starting late "
             "lets the model choose a layout and only then corrects it "
             "toward the guide."),

    ConfigField(key="frames.width", default=1024, label="Width", kind="int",
     min=512, max=2048, step=64, group="Frames",
     help="Keep equal to canonical.width — the anchor and the frames should "
             "be drawn at one scale."),
    ConfigField(key="frames.height", default=1024, label="Height", kind="int",
     min=512, max=2048, step=64, group="Frames",
     help="Keep equal to canonical.height, for the reason Width is kept "
             "equal — the anchor and the frames should be drawn at one "
             "scale, or the identity match is comparing different "
             "sizes."),
    ConfigField(key="frames.seed", label="Seed", kind="int",
     min=0, max=2147483647, group="Frames",
     help="Identical for every frame on purpose: same seed, same prompt, "
             "same anchor, DIFFERENT skeleton is the consistency recipe."),
    ConfigField(key="frames.lora_strength", default=1.2, label="Pixel LoRA strength",
     kind="float", min=0.0, max=2.0, step=0.05, group="Frames",
     help="The pixel LoRA's weight on the frames. Keep it equal to "
             "canonical.lora_strength: the frames are matched against "
             "the anchor, and a different style weight is one more "
             "difference that match has to absorb."),
    ConfigField(key="frames.negative", default='blurry, soft, smooth gradient, antialiased, jpeg artifacts, photo, realistic, 3d render, watermark, signature, text, extra limbs, deformed, low contrast, muddy colors', label="Negative prompt", kind="textarea",
     group="Frames",
     help="The base negative every frame starts from. The pose, backdrop and "
             "facing guards append to it rather than replace it, so "
             "what you add here survives all of them."),
    ConfigField(key="frames.controlnet.union_type", label="Pose control type",
     kind="text", group="Pose control",
     help="Blank follows the rig's own control channel, which is almost "
             "always what you want. Name one only to override, and know "
             "that naming the wrong one means the skeleton is quietly "
             "ignored rather than refused."),
    ConfigField(key="frames.controlnet.start_percent", default=0.0, label="ControlNet start %",
     kind="float", min=0.0, max=1.0, step=0.05, group="Pose control",
     help="Fraction of sampling before the pose control begins. The pose is "
             "the whole reason a frame differs from the anchor, so this "
             "normally starts at 0 — a late start gives the model time "
             "to commit to a pose of its own first."),
    ConfigField(key="frames.depth_controlnet.strength", default=0.45, label="Depth strength",
     kind="float", min=0.0, max=2.0, step=0.05, group="Pose control",
     help="Depth is the ONLY channel that carries the viewing angle — a 2D "
             "skeleton cannot express yaw. But the capsules are a bare body, "
             "so pushing this hard makes a costumed character collapse toward "
             "the naked silhouette: at 0.75 a veiled figure came back columnar."),
    ConfigField(key="frames.depth_controlnet.start_percent", default=0.0, label="Depth start %",
     kind="float", min=0.0, max=1.0, step=0.05, group="Pose control",
     help="When the depth channel joins. Depth is what carries the viewing "
             "angle, which a 2D skeleton cannot express, so a late "
             "start means the early steps commit to the wrong yaw."),
    ConfigField(key="frames.depth_controlnet.end_percent", default=0.6, label="Depth end %",
     kind="float", min=0.0, max=1.0, step=0.05, group="Pose control",
     help="Hold it long enough to survive the anchor, which runs to 100%."),
    ConfigField(key="frames.ip_adapter.start_at", default=0.0, label="Identity start %",
     kind="float", min=0.0, max=1.0, step=0.05, group="Identity",
     help="When identity conditioning joins. Late means the frame's layout "
             "is settled before the reference is consulted, which "
             "protects the pose at the cost of likeness."),
    ConfigField(key="frames.ip_adapter.end_at", default=1.0, label="Identity end %",
     kind="float", min=0.0, max=1.0, step=0.05, group="Identity",
     help="When identity conditioning stops. Releasing early lets the last "
             "steps sharpen without the reference pulling detail back "
             "toward itself; holding to 1.0 keeps the likeness "
             "tightest."),

    ConfigField(key="depth.size", label="Depth map size", kind="int",
     min=256, max=2048, step=64, group="Depth",
     help="Blank follows pose.size. Both are control images, so they should "
             "match the canvas they steer."),
    ConfigField(key="softbody.preroll_cycles", default=2, label="Preroll cycles", kind="int",
     min=0, max=16, group="Softbody",
     help="Cycles simulated before the first kept frame, so the wobble "
             "starts settled instead of springing from rest."),
]


def get_path(cfg: dict, path: str) -> Any:
    node: Any = cfg
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def set_path(cfg: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    node = cfg
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


def dynamic_options(root: Path) -> dict[str, list[str]]:
    """Options that depend on what's actually on disk or running."""
    from ..looks import poses as pose_lib
    from ..looks.palettes import discover

    poses = sorted(pose_lib.discover(root))
    palettes = sorted(discover(root))

    def weights(folder: str) -> list[str]:
        d = root / "ComfyUI" / "models" / folder
        return sorted(p.name for p in d.glob("*.safetensors")) if d.exists() else []

    models: list[str] = []
    try:
        from ..refs.llm import Ollama

        client = Ollama()
        if client.alive():
            models = client.models()
    except Exception:
        pass

    return {
        "poses": poses,
        "palettes": palettes,
        "views": sorted(VIEWS_FOR_UI),
        "ollama": models or ["qwen3:4b"],
        "rigs": [r["name"] for r in _rig_summaries()],
        "rigs_with_auto": ["auto"] + [r["name"] for r in _rig_summaries()],
        "style_names": _style_names(root),
        "rig_info": _rig_summaries(),
        "checkpoints": weights("checkpoints"),
        "loras": weights("loras"),
        "controlnets": weights("controlnet"),
        "ipadapters": weights("ipadapter"),
        "vae": weights("vae"),
    }


def _style_names(root: Path) -> list[str]:
    from ..looks.styles import discover

    return sorted(discover(root))


def _rig_summaries() -> list[dict]:
    from ..geometry.rigs import summaries

    return summaries()


def _render(field: ConfigField) -> dict:
    base = field.declared()
    modules = base.pop("modules")
    options_from = base.pop("options_from")
    free_numeric = base.pop("free_numeric")
    base["path"] = base.pop("key")
    base["type"] = base.pop("kind")
    base["group"] = base.pop("group")
    base["options"] = [list(o) if isinstance(o, (list, tuple)) else o
                        for o in field.options]
    # `del` unconditionally is why no field could carry its own default: a value declared beside the bounds it obeys was dropped before the form ever saw it, and only Stage.DEFAULTS could supply one — which reaches nothing nested, since _declared_default stops at one dot.
    if base["default"] is None:
        del base["default"]
    for key, empty in (("min", None), ("max", None), ("step", None),
                        ("options", []), ("when", {})):
        if base[key] == empty:
            del base[key]
    if modules:
        base["modules"] = modules
    if options_from:
        base["options_from"] = options_from
    if free_numeric:
        base["free_numeric"] = free_numeric
    return base


@dataclass
class ConfigSchema:
    """The pipeline settings surface: its fields, and every operation on them."""

    fields: list[ConfigField]
    modules: dict[str, dict[str, Any]]

    def fields_for(self, module: str | None) -> list[dict[str, Any]]:
        out = []
        for field in self.fields:
            scope = field.modules
            if scope and module and module not in scope:
                continue
            entry = _render(field)
            override = (entry.pop("help_for", None) or {}).get(module or "")
            if override:
                entry["help"] = override
            out.append(entry)
        return out

    def defaults_under(self, prefix: str) -> dict[str, Any]:
        """Every declared default below a dotted prefix, nested as the config nests it."""
        head = f"{prefix}."
        out: dict[str, Any] = {}
        for field in self.fields:
            if field.default is None or not field.key.startswith(head):
                continue
            node = out
            *branch, leaf = field.key[len(head):].split(".")
            for step in branch:
                node = node.setdefault(step, {})
            node[leaf] = field.default
        return out

    def describe(self, root: Path, module: str | None = None) -> dict[str, Any]:
        """The settings surface. Stages are not in it: this module describes fields, and reaching for the stage registry to list them is what made `schema` and `stage` import each other."""
        return {
            "module": module,
            "modules": self.modules,
            "fields": self.fields_for(module),
            "options": dynamic_options(root),
        }

    def get(self, cfg: dict, path: str) -> Any:
        return get_path(cfg, path)

    def set(self, cfg: dict, path: str, value: Any) -> None:
        set_path(cfg, path, value)

    def field(self, path: str) -> ConfigField | None:
        for f in self.fields:
            if f.key == path:
                return f
        return None

    def check(self, cfg: dict, _path: str = "") -> None:
        for key, value in (cfg or {}).items():
            here = f"{_path}.{key}" if _path else key
            f = self.field(here)
            if f is not None:
                f.check(value)
            elif isinstance(value, dict):
                self.check(value, here)

    def clamp(self, cfg: dict) -> tuple[dict, list[str]]:
        out = copy.deepcopy(cfg or {})
        notes: list[str] = []
        self._clamp_into(out, notes)
        return out, notes

    def _clamp_into(self, node: dict, notes: list[str], _path: str = "") -> None:
        for key, value in node.items():
            here = f"{_path}.{key}" if _path else key
            f = self.field(here)
            if f is not None:
                fixed = f.clamp(value)
                if fixed != value:
                    notes.append(f"'{here}' was {value!r}, clamped to {fixed!r}")
                node[key] = fixed
            elif isinstance(value, dict):
                self._clamp_into(value, notes, here)


SCHEMA = ConfigSchema(fields=FIELDS, modules=MODULES)


def fields_for(module: str | None) -> list[dict[str, Any]]:
    """Fields relevant to a pipeline kind, with any per-module rewording applied."""
    return SCHEMA.fields_for(module)


def describe(root: Path, module: str | None = None) -> dict[str, Any]:
    return SCHEMA.describe(root, module)
