
from __future__ import annotations


import base64
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..geometry.bodyspace import JOINTS, NEUTRAL, snap_to_anatomy, validate_pose
from ..shared.errors import Unavailable


class LLMError(Unavailable):
    """Ollama is unreachable, or gave an answer that could not be used."""


class Ollama:
    def __init__(
        self,
        host: str = "http://127.0.0.1:11434",
        model: str = "qwen3:4b",
        keep_alive: str | int = 0,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.keep_alive = keep_alive

    def alive(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=5) as r:
                return r.status == 200
        except Exception:
            return False

    def models(self) -> list[str]:
        with urllib.request.urlopen(f"{self.host}/api/tags", timeout=10) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]

    def look(
        self,
        prompt: str,
        images: list[Path],
        *,
        system: str = "",
        temperature: float = 0.2,
        timeout: float = 300,
    ) -> str:
        """Requires a VLM such as qwen2.5vl."""
        encoded = [
            base64.b64encode(Path(p).read_bytes()).decode() for p in images[:4]
        ]
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "images": encoded,
            "stream": False,
            "keep_alive": self.keep_alive,
            "think": False,
            "format": "json",
            "options": {"temperature": temperature},
        }
        if system:
            payload["system"] = system

        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read())
            return body.get("response") or body.get("thinking") or ""
        except urllib.error.HTTPError as e:
            raise LLMError(
                f"vision request failed ({e.code}). Is '{self.model}' a vision "
                f"model, and is it pulled? {e.read().decode(errors='replace')[:200]}"
            ) from e

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        temperature: float = 0.7,
        json_mode: bool = True,
        think: bool = False,
        timeout: float = 300,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": self.keep_alive,

            "think": think,
            "options": {"temperature": temperature},
        }
        if system:
            payload["system"] = system
        if json_mode:
            payload["format"] = "json"

        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read())
            return body.get("response") or body.get("thinking") or ""
        except urllib.error.HTTPError as e:
            raise LLMError(
                f"Ollama returned {e.code}: {e.read().decode(errors='replace')}"
            ) from e
        except urllib.error.URLError as e:
            raise LLMError(
                f"cannot reach Ollama at {self.host} ({e.reason}). Start it with "
                f"`ollama serve`."
            ) from e


POSE_SYSTEM = """You are a 2D character animator. You output ONLY JSON.

You place skeleton joints in BODY SPACE, a coordinate system attached to the
character (not the camera):

  lateral : the character's own left(+) / right(-) axis.  approx -0.10..0.10
  depth   : in front of(+) / behind(-) the character.     approx -0.20..0.25
  height  : 0.0 at the top of the head, 1.0 at the feet.  approx 0.10..0.85

Rules you must obey:
- Bone lengths are FIXED. A limb may swing anywhere, but an upper arm cannot
  get longer. Move joints in arcs around their parent.
- Keep both feet near height 0.82 unless the action is a jump.
- Only include joints you actually move; anything you omit keeps its neutral
  value.
- Animation frames must read as one continuous motion, in order.
"""

POSE_TEMPLATE = """Neutral pose (each joint is [lateral, depth, height]):
{neutral}

{example}Action: {action}
Frames: {frames}

Return JSON exactly like:
{{"frames": [{{"l_wrist": [0.05, -0.10, 0.24], "l_elbow": [0.06, -0.05, 0.33]}}, ...]}}

Return exactly {frames} frame object(s), in temporal order. Make the motion
large and readable — timid movement produces a lifeless animation. Move the
legs and torso too, not only the arms.{feedback}"""

# The full 18 would bury the signal in head keypoints that rarely move.
EXAMPLE_JOINTS = (
    "neck", "l_elbow", "l_wrist", "r_elbow", "r_wrist",
    "l_knee", "l_ankle", "r_knee", "r_ankle",
)


def load_example(path, take: int = 3) -> str:
    try:
        data = json.loads(path.read_text())
        frames = data.get("frames", [])
    except (OSError, json.JSONDecodeError):
        return ""
    if not frames:
        return ""

    step = max(1, len(frames) // take)
    picked = frames[::step][:take]
    trimmed = [
        {k: [round(float(x), 3) for x in v]
         for k, v in f.items() if k in EXAMPLE_JOINTS}
        for f in picked
    ]
    return (
        f"Example of a good '{data.get('name', 'action')}' "
        f"({data.get('description', '')}):\n"
        + json.dumps({"frames": trimmed})
        + "\n\nNote how far the joints travel between frames.\n\n"
    )


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise LLMError(f"no JSON object in model output:\n{text[:400]}")
    return json.loads(match.group(0))


def generate_pose(
    client: Ollama,
    action: str,
    frames: int,
    *,
    temperature: float = 0.7,
    attempts: int = 3,
    tolerance: float = 0.3,
    example_path: Any = None,
    verbose: bool = True,
) -> list[dict[str, list[float]]]:
    neutral = json.dumps({k: list(v) for k, v in NEUTRAL.items()}, indent=1)
    example = load_example(example_path) if example_path else ""
    feedback = ""

    for attempt in range(1, attempts + 1):
        raw = client.generate(
            POSE_TEMPLATE.format(
                neutral=neutral, action=action, frames=frames,
                example=example, feedback=feedback,
            ),
            system=POSE_SYSTEM,
            temperature=temperature,
            json_mode=True,
        )

        try:
            got = _extract_json(raw).get("frames")
        except (LLMError, json.JSONDecodeError) as e:
            feedback = f"\n\nYour previous reply was not valid JSON ({e}). Try again."
            if verbose:
                print(f"   attempt {attempt}: invalid JSON")
            continue

        if not isinstance(got, list) or not got:
            feedback = "\n\nYour previous reply had no 'frames' list. Try again."
            if verbose:
                print(f"   attempt {attempt}: no frames")
            continue

        merged, problems = [], []
        for i, frame in enumerate(got[:frames]):
            if not isinstance(frame, dict):
                problems.append(f"frame {i} is not an object")
                continue
            clean = {
                k: [float(x) for x in v]
                for k, v in frame.items()
                if k in JOINTS and isinstance(v, (list, tuple)) and len(v) == 3
            }
            pose = snap_to_anatomy({**{k: list(v) for k, v in NEUTRAL.items()}, **clean})
            issues = validate_pose(pose, tolerance)
            if issues:
                problems += [f"frame {i}: {p}" for p in issues[:3]]
            merged.append(pose)

        if len(merged) < frames:
            problems.append(f"got {len(merged)} frames, need {frames}")

        if not problems:
            if verbose:
                print(f"   pose accepted on attempt {attempt} ({len(merged)} frames)")
            return merged

        if verbose:
            print(f"   attempt {attempt} rejected: {problems[0]}")
        feedback = (
            "\n\nYour previous attempt was anatomically invalid:\n"
            + "\n".join(f"- {p}" for p in problems[:6])
            + "\nFix these. Remember bone lengths cannot change."
        )

    raise LLMError(
        f"could not get a valid pose for '{action}' in {attempts} attempts. "
        f"Last problems:{feedback}"
    )
