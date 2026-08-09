"""Importing this package registers every stage.

Stages self-register via the @register decorator, so the only thing needed to
make a new one reachable from config is an import here.
"""

from . import canonical, depth, export, frames, palette, pose, softbody  # noqa: F401

__all__ = ["canonical", "depth", "export", "frames", "palette", "pose", "softbody"]
