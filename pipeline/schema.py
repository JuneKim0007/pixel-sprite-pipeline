"""Machine-readable description of every configurable knob.

The GUI builds its forms from this, so a setting exists in exactly one place:
add a field here and it appears in the interface with the right control, range
and help text. Without it the "configurable UI" degenerates into a YAML
textarea, which is not configurability so much as a file editor.

`help` is the same explanation carried by the comments in configs/*.yaml —
these are the notes worth reading before changing a value.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .bodyspace import VIEWS as VIEWS_FOR_UI

# What a pipeline is for. Fields may scope themselves to a subset, so a T-pose
# character sheet does not show eight knobs about LLM-authored motion.
MODULES: dict[str, dict[str, str]] = {
    "animation": {
        "label": "Animation",
        "blurb": "A sequence of frames of one character performing an action.",
    },
    "character_sheet": {
        "label": "Character sheet",
        "blurb": "One reference pose seen from several angles. Usually the "
                 "first thing you make, and the input to an animation.",
    },
}


# type: text | textarea | int | float | bool | select | stages
# `modules` restricts a field to certain pipeline kinds; absent means all.
# `help_for` reworks the explanation per module where the meaning shifts.
FIELDS: list[dict[str, Any]] = [
    # ------------------------------------------------------------- identity
    {"path": "rig", "label": "Creature", "type": "select",
     "options_from": "rigs_with_auto", "group": "Asset",
     "help": "Body plan. Decides joint layout and which ControlNet channel the "
             "skeleton can be sent to. Only 'humanoid' has a matching OpenPose "
             "model; every other rig uses scribble + depth, and 'blob' uses "
             "depth alone."},
    {"modules": ["character_sheet"], "path": "detect.model", "label": "Vision model", "type": "text",
     "group": "Asset",
     "help": "Used only when Creature is 'auto'. Must be a vision model in "
             "Ollama, e.g. qwen2.5vl:3b. Classification only — a VLM is a poor "
             "judge of joint positions but a good judge of body plan."},
    {"modules": ["character_sheet"], "path": "detect.min_confidence", "label": "Min confidence", "type": "float",
     "min": 0.0, "max": 1.0, "step": 0.05, "group": "Asset",
     "help": "Below this the detection is discarded and humanoid is used. 0 "
             "accepts any answer."},
    {"path": "styles", "label": "Style sheets", "type": "styles",
     "options_from": "style_names", "group": "Asset",
     "help": "Named looks applied beneath this pipeline. A sheet may set "
             "prompts, palette, sampler values and exemplar images together, "
             "because all of those carry a look. Later sheets win over earlier "
             "ones, and the pipeline wins over all of them."},
    {"path": "annotate", "label": "Reference annotation", "type": "select",
     "options": ["skip", "if_present", "require"], "group": "Asset",
     "help": "Whether this pipeline uses per-image rig annotations. 'skip' "
             "ignores them, which is the right default for unattended runs. "
             "'if_present' uses any that exist. 'require' holds the job until "
             "every reference has one, for when you intend to mark them by "
             "hand before a long batch."},
    {"path": "name", "label": "Run name", "type": "text", "group": "Asset",
     "help": "Used for the output folder name."},
    {"path": "subject", "label": "Subject", "type": "textarea", "group": "Asset",
     "help": "The character. Read by every stage that writes a prompt, so it "
             "lives in one place."},
    {"path": "style", "label": "Style", "type": "textarea", "group": "Asset",
     "help": "Appended to the subject. 'plain flat background' makes the "
             "background keying in the palette stage much cleaner."},

    # --------------------------------------------------------------- order
    {"path": "pipeline.stop_after", "label": "Pause after stage", "type": "select",
     "options_from": "stage_names", "group": "Pipeline",
     "help": "Stop the run after this stage so its output can be reviewed or "
             "edited before the expensive stages consume it. Stopping after "
             "'pose' is what makes the rig editor useful — resume continues "
             "with whatever you saved."},
    {"path": "pipeline.stages", "label": "Stage order", "type": "stages",
     "group": "Pipeline",
     "help": "Validated before anything runs: a stage whose inputs aren't "
             "produced earlier is rejected with an explanation. Consecutive "
             "independent CPU stages run in parallel; GPU stages never do."},

    # ---------------------------------------------------------------- pose
    {"path": "pose.source", "label": "Pose source", "type": "select",
     "options": ["library", "llm", "tpose"], "group": "Pose",
     "help": "'library' uses hand-authored poses (best quality). 'llm' drafts "
             "a new action from text and caches it. 'tpose' synthesises the "
             "rig's reference pose — what a character sheet uses."},
    {"modules": ["animation"], "path": "pose.name", "label": "Library pose", "type": "select",
     "options_from": "poses", "group": "Pose", "when": {"pose.source": "library"},
     "help": "A file in poses/. Regenerate with tools/make_poses.py."},
    {"modules": ["animation"], "path": "pose.action", "label": "Action (LLM)", "type": "textarea",
     "group": "Pose", "when": {"pose.source": "llm"},
     "help": "Plain description of the motion. Be explicit about the ending "
             "position — 'ending in follow-through' beats 'attacking'."},
    {"path": "pose.symmetric", "label": "Symmetric reference pose",
     "type": "bool", "modules": ["character_sheet"], "group": "Pose",
     "help": "Off by default, and worth leaving off. A mirrored T-pose puts two "
             "horizontal limbs at shoulder height, and a prompt mentioning a "
             "sword or staff often comes back with blades drawn along them "
             "instead of arms. Arms-down also matches how references are "
             "usually posed."},
    {"path": "pose.view", "label": "Viewing angle", "type": "select",
     "options": ["front", "three_quarter_front", "side", "three_quarter_rear",
                 "rear_turned", "rear"],
     "free_numeric": True, "group": "Pose",
     "help": "Poses are stored in body space and projected here, so one pose "
             "file serves every camera. Accepts a raw angle too (e.g. 160). "
             "Past ~100 degrees the face keypoints drop automatically, which "
             "is how the skeleton tells ControlNet the character faces away."},
    {"path": "pose.frames", "label": "Frame count", "type": "int",
     "min": 1, "max": 24, "group": "Pose",
     "help": "Blank uses every frame in the pose file."},
    {"path": "pose.size", "label": "Skeleton size", "type": "int",
     "min": 256, "max": 2048, "step": 64, "group": "Pose",
     "help": "Should match the generation resolution."},
    {"path": "pose.depth_scale", "label": "Depth exaggeration", "type": "float",
     "min": 0.0, "max": 2.5, "step": 0.05, "group": "Pose",
     "help": "Scales forward/back motion without re-authoring the pose. Above "
             "1.0 makes a swing reach further."},
    {"path": "pose.lateral_scale", "label": "Stance width", "type": "float",
     "min": 0.0, "max": 2.5, "step": 0.05, "group": "Pose",
     "help": "Scales the character's left/right spread."},

    # --------------------------------------------------------------- depth
    #
    # Depth carries the viewing angle, which a skeleton alone cannot express,
    # and it is the only control channel that works for every topology — we
    # compute it from body space rather than estimating it from an image, so a
    # serpent or a blob gets a correct one where OpenPose has nothing to say.
    {"path": "depth.near", "label": "Nearest brightness", "type": "int",
     "min": 0, "max": 255, "step": 5, "group": "Depth",
     "help": "Grey value for the part of the body closest to the camera. "
             "255 is the convention the ControlNet was trained on."},
    {"path": "depth.far", "label": "Farthest brightness", "type": "int",
     "min": 0, "max": 255, "step": 5, "group": "Depth",
     "help": "Grey value for the farthest part. Raising it flattens the map, "
             "which weakens the sense of rotation; lowering it past ~40 starts "
             "losing the far limb into the background."},
    {"path": "depth.blur", "label": "Softness", "type": "float",
     "min": 0.0, "max": 24.0, "step": 0.5, "group": "Depth",
     "help": "Gaussian radius over the rendered limbs. Some blur is wanted: a "
             "hard-edged depth map reads as geometry and the model draws tubes."},

    # ----------------------------------------------------------------- llm
    {"modules": ["animation"], "path": "pose.llm.model", "label": "Model", "type": "select",
     "options_from": "ollama", "group": "LLM", "when": {"pose.source": "llm"},
     "help": "Must be pulled in Ollama. Bigger models write better motion."},
    {"modules": ["animation"], "path": "pose.llm.temperature", "label": "Temperature", "type": "float",
     "min": 0.0, "max": 2.0, "step": 0.05, "group": "LLM",
     "when": {"pose.source": "llm"},
     "help": "Creativity of the LLM only. This has nothing to do with image "
             "variation — that is CFG and seed."},
    {"modules": ["animation"], "path": "pose.llm.attempts", "label": "Retry attempts", "type": "int",
     "min": 1, "max": 8, "group": "LLM", "when": {"pose.source": "llm"},
     "help": "Rejected poses are fed back as explicit criticism and retried."},
    {"modules": ["animation"], "path": "pose.llm.tolerance", "label": "Anatomy tolerance", "type": "float",
     "min": 0.05, "max": 1.0, "step": 0.05, "group": "LLM",
     "when": {"pose.source": "llm"},
     "help": "Allowed bone-length drift after anatomy snapping. Tighter is "
             "stricter; snapping already makes bones near-exact."},
    {"modules": ["animation"], "path": "pose.llm.cache", "label": "Cache accepted poses", "type": "bool",
     "group": "LLM", "when": {"pose.source": "llm"},
     "help": "Writes to poses/generated/ so a good sequence is reusable."},

    # ----------------------------------------------------------- canonical
    {"path": "canonical.seed", "label": "Seed", "type": "int",
     "min": 0, "max": 2147483647, "group": "Canonical",
     "help": "PIN THIS. Every frame reuses it; changing it changes the "
             "character entirely."},
    {"path": "canonical.lcm", "label": "Fast LCM mode", "type": "bool",
     "group": "Canonical",
     "help": "Off for the canonical. LCM is ~3x faster but drops prompt "
             "details — it lost 'holding a sword' in testing."},
    {"path": "canonical.steps", "label": "Steps", "type": "int",
     "min": 1, "max": 150, "group": "Canonical",
     "help": "25 for quality. Below ~8 you pay fixed overhead for worse output."},
    {"path": "canonical.cfg", "label": "CFG", "type": "float",
     "min": 1.0, "max": 14.0, "step": 0.1, "group": "Canonical",
     "help": "Prompt adherence. 7 is the SDXL sweet spot; use 1.5 with LCM."},
    {"path": "canonical.lora_strength", "label": "Pixel LoRA strength",
     "type": "float", "min": 0.0, "max": 2.0, "step": 0.05, "group": "Canonical",
     "help": "1.2 is the pixel-art-xl author's recommendation."},
    {"path": "canonical.width", "label": "Width", "type": "int",
     "min": 512, "max": 2048, "step": 64, "group": "Canonical",
     "help": "SDXL is trained at 1024; smaller degrades quality and saves less "
             "time than you'd expect."},
    {"path": "canonical.height", "label": "Height", "type": "int",
     "min": 512, "max": 2048, "step": 64, "group": "Canonical"},

    # -------------------------------------------------------------- frames
    {"path": "frames.lcm", "label": "Fast LCM mode", "type": "bool",
     "group": "Frames",
     "help": "On for drafts, off for the final render."},
    {"path": "frames.steps", "label": "Steps", "type": "int",
     "min": 1, "max": 150, "group": "Frames"},
    {"path": "frames.cfg", "label": "CFG", "type": "float",
     "min": 1.0, "max": 14.0, "step": 0.1, "group": "Frames",
     "help": "1.5 with LCM. Using 7 with LCM burns the image."},
    {"path": "frames.denoise", "label": "Denoise", "type": "float",
     "min": 0.0, "max": 1.0, "step": 0.05, "group": "Frames",
     "help": "1.0 generates fresh. Below 1.0 carries over the input latent — "
             "this is the 'how much of the previous step survives' dial."},
    {"path": "frames.guard_against_skeletons", "label": "Guard against tracing",
     "type": "bool", "group": "Pose control",
     "help": "Appends anti-skeleton terms to the negative prompt whenever a "
             "pose guide is used. Without it the model sometimes draws the "
             "guide itself — measured: a T-pose produced a figure with swords "
             "where its arms should be, and an undead-looking body."},
    {"path": "frames.controlnet.enabled", "label": "Use the pose guide",
     "type": "bool", "group": "Pose control",
     "help": "Off is right for a standing character sheet. Measured: with it "
             "on, legs came back as white shafts with ball joints — the guide "
             "drawn as bones — while depth alone gave armoured legs and boots. "
             "Leave it on for attack, hit and fall, where the pose is the "
             "point."},
    {"path": "frames.controlnet.strength", "label": "ControlNet strength",
     "type": "float", "min": 0.0, "max": 2.0, "step": 0.05, "group": "Pose control",
     "help": "How hard the pose guide is enforced. Above ~0.85 the model starts "
             "tracing the guide instead of using it, which is what produces "
             "stick-figure or skeletal output."},
    {"path": "frames.controlnet.end_percent", "label": "ControlNet end %",
     "type": "float", "min": 0.0, "max": 1.0, "step": 0.05, "group": "Pose control",
     "help": "Fraction of sampling the guide steers for. 0.55 lands the pose "
             "and leaves the second half for the LoRA to render a character "
             "over it; holding it longer produces a traced stick figure."},
    {"path": "frames.ip_adapter.weight", "label": "IP-Adapter weight",
     "type": "float", "min": 0.0, "max": 1.5, "step": 0.05, "group": "Identity",
     "help": "0.3-0.5 style nudge | 0.6 mixed | 0.8-1.0 identity lock. Too "
             "high looks pasted on and fights the pose."},
    {"path": "frames.ip_adapter.weight_type", "label": "Weight type",
     "type": "select", "group": "Identity",
     "options": ["standard", "prompt is more important", "style transfer",
                 "composition", "style and composition"],
     "help": "How the reference is blended into conditioning."},

    # ------------------------------------------------------------- palette
    {"modules": ["animation"], "path": "softbody.fps", "label": "Playback fps",
     "type": "float", "min": 1, "max": 60, "step": 1, "group": "Softbody",
     "help": "The rate the spring simulation assumes when it computes lag."},
    {"modules": ["animation"], "path": "softbody.loop", "label": "Cyclic motion",
     "type": "bool", "group": "Softbody",
     "help": "Pre-rolls the simulation so frame 0 is not sitting at rest, which "
             "otherwise reads as a hitch every time the loop restarts."},
    {"path": "palette.source", "label": "Palette source", "type": "select",
     "options": ["extract", "file", "llm"], "group": "Palette",
     "help": "'extract' derives from the canonical and always matches the art. "
             "'file' reuses a committed palette, keeping separate runs of the "
             "same character on-model. 'llm' chooses one by subject — only "
             "chooses; applying it stays deterministic, which is what stops "
             "frames drifting in colour."},
    {"path": "palette.file", "label": "Palette file", "type": "select",
     "options_from": "palettes", "group": "Palette",
     "when": {"palette.source": "file"}},
    {"path": "palette.size", "label": "Palette size", "type": "int",
     "min": 2, "max": 64, "group": "Palette",
     "help": "Number of colours the whole animation is quantised to."},
    {"path": "palette.factor", "label": "Downscale factor", "type": "int",
     "min": 2, "max": 16, "group": "Palette",
     "help": "8 gives 128px sprites from 1024. Verified as this LoRA's true "
             "pixel grid by an intra-block variance sweep."},
    {"path": "palette.reduce", "label": "Block reduction", "type": "select",
     "options": ["median", "mode", "mean"], "group": "Palette",
     "help": "median measured 100% structural accuracy against ground truth; "
             "mode only 70% (noise makes every colour unique)."},
    {"path": "palette.alpha_tolerance", "label": "Background tolerance",
     "type": "int", "min": 0, "max": 128, "group": "Palette",
     "help": "Edge-connected background within this tolerance becomes "
             "transparent. Enclosed regions are preserved."},
    {"path": "palette.upscale", "label": "Frame upscale", "type": "int",
     "min": 1, "max": 16, "group": "Palette",
     "help": "Nearest-neighbour, for viewing."},
    {"path": "palette.workers", "label": "Worker processes", "type": "int",
     "min": 1, "max": 16, "group": "Palette",
     "help": "Blank uses one per core. Frames are independent."},

    {"path": "palette.dither", "label": "Dither", "type": "bool", "group": "Palette",
     "help": "Trades flat blocks for apparent colour depth. Leave off for the "
             "chunky RPG-Maker idiom; turn on when a small palette has to "
             "carry a gradient."},
    {"path": "palette.phase", "label": "Pixel grid origin", "type": "select",
     "options": ["per_frame", "locked"], "group": "Palette",
     "help": "'per_frame' finds the best grid for each image independently. "
             "'locked' measures it once on the canonical and applies it to "
             "every frame — which is what an animation wants, since a grid "
             "that shifts by one pixel between frames makes the sprite "
             "shimmer."},

    # -------------------------------------------------------------- export
    {"path": "export.columns", "label": "Sheet columns", "type": "int",
     "min": 1, "max": 32, "group": "Export",
     "help": "Blank puts every frame in one row."},
    {"path": "export.scale", "label": "Sheet upscale", "type": "int",
     "min": 1, "max": 16, "group": "Export"},

    # --------------------------------------------------------------- infra
    {"path": "comfy.host", "label": "ComfyUI host", "type": "text",
     "group": "Services"},
    {"modules": ["animation"], "path": "pose.llm.host", "label": "Ollama host", "type": "text",
     "group": "Services"},

    # ---------------------------------------------------------- references
    {"path": "references.from_run", "label": "Inherit from run", "type": "text",
     "group": "References",
     "help": "A previous run whose output becomes this run's identity and "
             "palette. How a character sheet feeds an animation without "
             "regenerating the character."},
    {"path": "frames.style_weight", "label": "Style exemplar strength",
     "type": "float", "min": 0.0, "max": 0.8, "step": 0.05, "group": "References",
     "help": "How hard style references push. Deliberately an order of "
             "magnitude below identity — at identity strength a style exemplar "
             "replaces your character with the exemplar."},
    {"path": "references.match.tolerance_degrees", "label": "Match tolerance",
     "type": "float", "min": 0.0, "max": 180.0, "step": 5.0, "group": "References",
     "help": "A reference within this many degrees of the frame's viewing "
             "angle counts as a match and gets full weight."},
    {"path": "references.match.exact_weight", "label": "Weight when matched",
     "type": "float", "min": 0.0, "max": 1.5, "step": 0.05, "group": "References",
     "help": "IP-Adapter weight when a reference matches the view. High is "
             "correct here — you have real evidence of what that side looks "
             "like."},
    {"path": "references.match.far_weight", "label": "Weight when unmatched",
     "type": "float", "min": 0.0, "max": 1.5, "step": 0.05, "group": "References",
     "help": "Weight at a full 180-degree mismatch, interpolated in between. "
             "Keep this LOW. Forcing a front reference onto a rear generation "
             "produces a front-facing sprite that fights the pose; a weak hint "
             "leaves the model free to invent the unseen side."},
    {"path": "references.match.auto", "label": "Automatic falloff", "type": "bool",
     "group": "References",
     "help": "On: weight is chosen per frame from angular distance. Off: the "
             "fixed Identity weight is used for every frame."},


    # ------------------------------------------------------------- quality
    # Compute-heavy knobs. Ceilings are generous on purpose: the same config
    # runs on a laptop and on a lab machine, and only the numbers change.
    {"path": "canonical.sampler", "label": "Sampler", "type": "select",
     "options": ['dpmpp_2m', 'dpmpp_2m_sde', 'dpmpp_3m_sde', 'euler', 'euler_ancestral', 'heun', 'dpm_2', 'ddim', 'uni_pc', 'lcm'], "group": "Quality",
     "help": "dpmpp_2m is the SDXL workhorse. dpmpp_3m_sde and uni_pc cost "
             "more per step and can resolve finer detail. Must be 'lcm' when "
             "LCM mode is on."},
    {"path": "canonical.scheduler", "label": "Scheduler", "type": "select",
     "options": ['karras', 'normal', 'simple', 'sgm_uniform', 'exponential', 'beta'], "group": "Quality",
     "help": "karras concentrates steps where they matter most. sgm_uniform is "
             "required by LCM."},
    {"path": "frames.sampler", "label": "Sampler (frames)", "type": "select",
     "options": ['dpmpp_2m', 'dpmpp_2m_sde', 'dpmpp_3m_sde', 'euler', 'euler_ancestral', 'heun', 'dpm_2', 'ddim', 'uni_pc', 'lcm'], "group": "Quality"},
    {"path": "frames.scheduler", "label": "Scheduler (frames)", "type": "select",
     "options": ['karras', 'normal', 'simple', 'sgm_uniform', 'exponential', 'beta'], "group": "Quality"},
    {"path": "canonical.candidates", "label": "Canonical candidates", "type": "int",
     "min": 1, "max": 8, "group": "Quality",
     "help": "Generate this many canonical sprites in one batch and keep them "
             "all, so you can pick the best identity anchor instead of "
             "re-rolling. Batching amortises the ~35s fixed cost per prompt."},

    # --------------------------------------------------------- proportions
    # Bone-group multipliers on top of the body plan, so a long-necked variant
    # is a config change rather than a new rig. Everything below a lengthened
    # bone moves with it, keeping the skeleton connected.
    {"path": "proportions.head", "label": "Head size", "type": "float",
     "min": 0.3, "max": 3.0, "step": 0.05, "group": "Proportions",
     "help": "Also grows the skull in the depth map, which is where head volume actually reads."},
    {"path": "proportions.neck", "label": "Neck length", "type": "float",
     "min": 0.3, "max": 3.0, "step": 0.05, "group": "Proportions",
     "help": "1.0 is the rig's default. 2.0 gives a giraffe-like or serpentine neck."},
    {"path": "proportions.torso", "label": "Torso length", "type": "float",
     "min": 0.3, "max": 3.0, "step": 0.05, "group": "Proportions",
     "help": "Stretches the spine; the limbs follow their anchors."},
    {"path": "proportions.arms", "label": "Arm length", "type": "float",
     "min": 0.3, "max": 3.0, "step": 0.05, "group": "Proportions",
     "help": "Long-armed apes, short-armed brawlers."},
    {"path": "proportions.legs", "label": "Leg length", "type": "float",
     "min": 0.3, "max": 3.0, "step": 0.05, "group": "Proportions",
     "help": "Below 1.0 gives a stubby, chibi stance."},
    {"path": "proportions.tail", "label": "Tail length", "type": "float",
     "min": 0.3, "max": 3.0, "step": 0.05, "group": "Proportions",
     "help": "Ignored by rigs with no tail."},
    {"path": "proportions.wings", "label": "Wing span", "type": "float",
     "min": 0.3, "max": 3.0, "step": 0.05, "group": "Proportions",
     "help": "Ignored by rigs with no wings."},
    {"path": "proportions.tentacles", "label": "Tentacle length", "type": "float",
     "min": 0.3, "max": 3.0, "step": 0.05, "group": "Proportions",
     "help": "Octopoid rigs only."},
    {"path": "proportions.segments", "label": "Segment length", "type": "float",
     "min": 0.3, "max": 3.0, "step": 0.05, "group": "Proportions",
     "help": "Serpent and centipede rigs only."},

    # --------------------------------------------------------------- props
    # Props live in a list editor (see fields.js); this entry documents the
    # group so the settings sidebar has somewhere to put it.
    {"path": "props._doc", "label": "Held objects", "type": "text",
     "group": "Props",
     "help": "Objects attached to a joint, inheriting its motion. Drawn into "
             "the depth map rather than the pose guide: OpenPose reads an "
             "extra chain as a broken limb, and a definite volume is what "
             "stops the model guessing where a weapon goes."},

    # -------------------------------------------------------------- models
    # Per-pipeline weight selection: a lighter pipeline can point at a smaller
    # LLM or a different LoRA without touching the others.
    {"path": "models.checkpoint", "label": "Checkpoint", "type": "select",
     "options_from": "checkpoints", "group": "Models",
     "help": "Base diffusion weights. SDXL is what the pixel LoRA was trained "
             "against; swapping the base usually means swapping the LoRA too."},
    {"path": "models.pixel_lora", "label": "Style LoRA", "type": "select",
     "options_from": "loras", "group": "Models"},
    {"path": "models.controlnet", "label": "ControlNet", "type": "select",
     "options_from": "controlnets", "group": "Models",
     "help": "The Union ProMax model covers openpose, depth, scribble and more "
             "in one file, which keeps memory down versus one model per type."},
    {"path": "models.ipadapter", "label": "IP-Adapter", "type": "select",
     "options_from": "ipadapters", "group": "Models"},

    # ------------------------------------------------------------- compute
    # NOTE: there is deliberately no "GPU cores" control. Metal exposes no way
    # to partition the GPU between processes — no equivalent of
    # CUDA_VISIBLE_DEVICES or MIG. The honest levers are memory ceiling, VRAM
    # policy, CPU threads, and how much work you ask for.
    {"path": "compute.vram_mode", "label": "VRAM policy", "type": "select",
     "options": ["gpu-only", "highvram", "normalvram", "lowvram", "cpu"],
     "group": "Compute",
     "help": "Passed to ComfyUI at launch. 'gpu-only' keeps text encoders on "
             "the GPU (~8% faster here). 'lowvram' offloads aggressively — "
             "slower, but survives bigger models. Requires a restart."},
    {"path": "compute.mps_high_watermark", "label": "MPS memory ceiling",
     "type": "float", "min": 0.0, "max": 1.0, "step": 0.05, "group": "Compute",
     "help": "Fraction of unified memory PyTorch may allocate "
             "(PYTORCH_MPS_HIGH_WATERMARK_RATIO). 0.0 disables the limit. "
             "Lower it to leave room for other apps; too low causes OOM."},
    {"path": "compute.torch_threads", "label": "CPU threads (torch)",
     "type": "int", "min": 1, "max": 16, "group": "Compute",
     "help": "Threads for CPU-side tensor work. The M4 has 4 performance and "
             "6 efficiency cores; more threads is not always faster."},
    {"path": "compute.cpu_workers", "label": "CPU worker processes",
     "type": "int", "min": 1, "max": 16, "group": "Compute",
     "help": "Parallel processes for CPU stages such as pixelization. "
             "Overrides palette.workers when set."},
    {"path": "compute.batch", "label": "Batch size", "type": "int",
     "min": 1, "max": 8, "group": "Compute",
     "help": "Images per queued prompt. Each prompt costs ~35s of fixed "
             "overhead regardless of steps, so batching amortises it: batch 4 "
             "measured 49s/image versus 71s one at a time."},
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
    poses = sorted(p.stem for p in (root / "poses").glob("*.json"))
    generated = sorted(
        f"generated/{p.stem}" for p in (root / "poses" / "generated").glob("*.json")
    )

    from .palettes import discover

    palettes = sorted(discover(root))

    def weights(folder: str) -> list[str]:
        d = root / "ComfyUI" / "models" / folder
        return sorted(p.name for p in d.glob("*.safetensors")) if d.exists() else []

    models: list[str] = []
    try:
        from .llm import Ollama

        client = Ollama()
        if client.alive():
            models = client.models()
    except Exception:
        pass

    return {
        "poses": poses + generated,
        "palettes": palettes,
        "views": sorted(VIEWS_FOR_UI),
        "ollama": models or ["qwen3:4b"],
        "stage_names": sorted(_stage_names()),
        "rigs": [r["name"] for r in _rig_summaries()],
        "rigs_with_auto": ["auto"] + [r["name"] for r in _rig_summaries()],
        "style_names": _style_names(root),
        "rig_info": _rig_summaries(),
        "checkpoints": weights("checkpoints"),
        "loras": weights("loras"),
        "controlnets": weights("controlnet"),
        "ipadapters": weights("ipadapter"),
    }


def _style_names(root: Path) -> list[str]:
    from .styles import discover

    return sorted(discover(root))


def _rig_summaries() -> list[dict]:
    from .rigs import summaries

    return summaries()


def _stage_names() -> list[str]:
    from .stage import available

    return list(available())


def fields_for(module: str | None) -> list[dict[str, Any]]:
    """Fields relevant to a pipeline kind, with any per-module rewording applied."""
    out = []
    for field in FIELDS:
        scope = field.get("modules")
        if scope and module and module not in scope:
            continue
        entry = dict(field)
        override = (entry.pop("help_for", None) or {}).get(module or "")
        if override:
            entry["help"] = override
        out.append(entry)
    return out


def describe(root: Path, module: str | None = None) -> dict[str, Any]:
    from .stage import available

    return {
        "module": module,
        "modules": MODULES,
        "fields": fields_for(module),
        "options": dynamic_options(root),
        "stages": [
            {
                "name": name,
                "resource": cls.resource,
                "requires": sorted(cls.requires),
                "produces": sorted(cls.produces),
            }
            for name, cls in sorted(available().items())
        ],
    }
