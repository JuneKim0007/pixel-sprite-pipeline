"""Importing this package registers every stage."""

from . import canonical, depth, export, frames, palette, pose, softbody  # noqa: F401

__all__ = ["canonical", "depth", "export", "frames", "palette", "pose", "softbody"]
