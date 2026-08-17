"""What went wrong, and whose fault it was.

A ValueError reaching the API is a bug; a PixelError is a message to the user.
Without that distinction the handler can only catch Exception and guess, so
someone typing nothing into a box got:

    500  {"error": "ValueError: a note needs some text"}

The status is a property of the error:

    NotFound     404   the thing named does not exist
    Invalid      400   the value is wrong, and the caller can fix it
    Denied       403   the path is outside what the browser may read
    Conflict     409   the request contradicts current state
    Unavailable  503   a service this needs is down
    Internal     500   a real defect, and the only one worth a stack trace

`hint` is separate from the message: the message says what happened, the hint
says what to do, and only some failures have a useful answer to that.
"""

from __future__ import annotations


class PixelError(Exception):
    """A failure this system understands well enough to describe.

    Anything not deriving from it is, by definition, unexpected.
    """

    status = 500
    kind = "error"

    def __init__(self, message: str, *, hint: str = "", **detail):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.detail = detail

    def as_dict(self) -> dict:
        out = {"error": self.message, "kind": self.kind}
        if self.hint:
            out["hint"] = self.hint
        if self.detail:
            out["detail"] = self.detail
        return out


class NotFound(PixelError):
    """A named thing does not exist, with the alternatives named.

    "no style sheet 'retro_jrpg2'" answers a different question from
    "available: base_pixel, retro_jrpg", and the second is usually the one
    being asked.
    """

    status = 404
    kind = "not_found"

    def __init__(self, what: str, name: str, *, available=None, hint: str = ""):
        options = sorted(available) if available else []
        message = f"no {what} '{name}'"
        if options:
            shown = ", ".join(options[:12])
            more = f" and {len(options) - 12} more" if len(options) > 12 else ""
            hint = hint or f"available: {shown}{more}"
        super().__init__(message, hint=hint, what=what, name=name)


class Invalid(PixelError):
    """A value is wrong and the caller can fix it."""

    status = 400
    kind = "invalid"

    def __init__(self, message: str, *, field: str = "", hint: str = ""):
        super().__init__(message, hint=hint, **({"field": field} if field else {}))


class Denied(PixelError):
    """A path outside what the browser is allowed to touch."""

    status = 403
    kind = "denied"


class TooLarge(PixelError):
    """The request is refused because computing it would not fit.

    Distinct from Invalid: nothing here is malformed. The numbers are all in
    range and the answer is simply bigger than this machine can hold, which is
    a different thing to tell someone and a different thing to do about it.

    Refused before allocating rather than after. A 413 costs a rejected
    request; discovering the same fact by allocating is what took the machine
    down - see docs/DECISIONS.md.
    """

    status = 413
    kind = "too_large"

    def __init__(self, message: str, *, field: str = "", hint: str = "", **detail):
        super().__init__(message, hint=hint,
                         **({"field": field} if field else {}), **detail)


class Conflict(PixelError):
    """The request contradicts current state: a running job cannot be moved."""

    status = 409
    kind = "conflict"


class Unavailable(PixelError):
    """A dependency is not running.

    Distinct from a defect: ComfyUI being down is not a bug in this code, which
    is why the queue pauses rather than failing every job.
    """

    status = 503
    kind = "unavailable"


class Internal(PixelError):
    """A real defect. The only kind worth a stack trace."""

    status = 500
    kind = "internal"


# Exceptions raised by code we do not own, mapped to what they mean to a caller.
#
# This is not scaffolding, though it was written as though it were. Four of
# these seven have had no raise site in this codebase from the start:
# open() and os raise the permission and directory errors, and urllib raises
# TimeoutError from the ComfyUI client - which is what the 504 is for.
#
# So an entry with no raise site here is not a finished migration, and deleting
# one on those grounds regresses a real status to 500. Our own raise sites are
# named by tools/check_failures.py; this table is for everyone else's.
BUILTIN_STATUS: dict[type, int] = {
    FileNotFoundError: 404,
    PermissionError: 403,
    NotADirectoryError: 400,
    IsADirectoryError: 400,
    ValueError: 400,
    KeyError: 404,
    TimeoutError: 504,
}


def status_for(exc: BaseException) -> int:
    """The HTTP status an exception deserves.

    ValueError maps to 400 rather than 500: of the 39 sites raising one,
    essentially all reject input, and reporting bad input as a server fault is
    worse than the reverse.
    """
    if isinstance(exc, PixelError):
        return exc.status
    for cls, status in BUILTIN_STATUS.items():
        if isinstance(exc, cls):
            return status
    return 500


def body_for(exc: BaseException) -> dict:
    """The JSON a failure becomes.

    An unnamed failure shows its class name, which is the signal that a raise
    site still needs converting.
    """
    if isinstance(exc, PixelError):
        return exc.as_dict()
    return {"error": f"{type(exc).__name__}: {exc}", "kind": "internal"}
