"""Importing this package registers every stage."""

from . import (  # noqa: F401
    canonical, depth, export, frames, palette, pixelise, pose, softbody,
)

__all__ = ["canonical", "depth", "export", "frames", "palette", "pixelise",
           "pose", "softbody"]
