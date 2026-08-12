"""Code with no dependency on any module.

That is the whole membership rule, and it is checkable rather than a matter of
taste: anything here that grows an import of a sibling module is a mistake the
import graph will show. A test asserts it.
"""

from .config import opt
from .errors import (BUILTIN_STATUS, Conflict, Denied, Internal, Invalid,
                     NotFound, PixelError, Unavailable, body_for, status_for)

__all__ = ["opt", "PixelError", "NotFound", "Invalid", "Denied", "Conflict",
           "Unavailable", "Internal", "BUILTIN_STATUS", "status_for", "body_for"]
