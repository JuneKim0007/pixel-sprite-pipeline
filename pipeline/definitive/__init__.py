"""The definitive layer: turning a generated image into real pixel art.

Everything the editor does lives under this package, because it is genuinely
separate from generation. Generation asks a model for something new; this asks
nothing of any model and produces the same output twice. It is the half of the
pipeline that is deterministic, and keeping it in one folder makes that visible.

    layers.py    the registry: what a layer is, and what a field is
    builtin.py   the five that ship
    run.py       executing a stack

Importing this package registers the built-in layers, which is why `builtin`
is imported for its side effect and not for a name.
"""

from . import builtin  # noqa: F401  (registers the built-in layers)
from .layers import (Field, LayerSpec, REGISTRY, catalogue, check_order,
                     default_stack, layer)
from .run import apply_stack

__all__ = ["Field", "LayerSpec", "REGISTRY", "catalogue", "check_order",
           "default_stack", "layer", "apply_stack"]
