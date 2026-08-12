"""What went wrong, and whose fault it was.

The distinction this exists to make: **a `ValueError` reaching the API is a
bug, and a `PixelError` is a message to the user.** Today they are
indistinguishable, so both come back as 500 with a class name in the body:

    POST /api/style/note {"text": "  "}
    500  {"error": "ValueError: a note needs some text"}

That is a person typing nothing into a box, reported as a server fault. The
server cannot do better because the exception carries no information about what
kind of failure it is - so the handler catches `Exception` and guesses, three
times over, in three copies of the same block.

So the status is a property of the error, not of the handler:

    NotFound     404   the thing named does not exist
    Invalid      400   the value is wrong, and the caller can fix it
    Denied       403   the path is outside what the browser may read
    Conflict     409   the request contradicts current state
    Unavailable  503   a service this needs is down; nothing is anyone's fault
    Internal     500   a real defect, and the only one worth a stack trace

Six exception classes already existed - QueueError, PathDenied, PipelineError,
LLMError, ComfyError, StyleError - with no common base and almost no use: 79 of
114 raise sites are builtins. They keep working, and gain a place in the
hierarchy, so nothing has to be converted in one pass. The count of remaining
builtin raises is the progress bar.

`hint` is separate from the message on purpose. The message says what happened;
the hint says what to do, and only some failures have a useful answer to that.
Blending them produces the paragraph-length error strings that people stop
reading.
"""

from __future__ import annotations


class PixelError(Exception):
    """A failure this system understands well enough to describe.

    Anything not deriving from this is, by definition, unexpected - which is
    what makes `except PixelError` a meaningful thing to write and
    `except Exception` a thing to avoid.
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
    """A named thing does not exist.

    Carries what was looked for and what was available, because "no style
    sheet 'retro_jrpg2'" answers a different question from "available:
    base_pixel, retro_jrpg, hi_fidelity" and the second is usually the one
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
    """A value is wrong and the caller can fix it.

    The commonest case by far, and the one currently reported as a 500.
    """

    status = 400
    kind = "invalid"

    def __init__(self, message: str, *, field: str = "", hint: str = ""):
        super().__init__(message, hint=hint, **({"field": field} if field else {}))


class Denied(PixelError):
    """A path outside what the browser is allowed to touch."""

    status = 403
    kind = "denied"


class Conflict(PixelError):
    """The request contradicts the current state.

    A job that is running cannot be moved; a config edited by another session
    cannot be overwritten blind.
    """

    status = 409
    kind = "conflict"


class Unavailable(PixelError):
    """Something this depends on is not running.

    Distinct from a defect: ComfyUI being down is not a bug in this code, and
    the queue already treats it that way by pausing rather than failing jobs.
    A 503 says the same thing to a browser.
    """

    status = 503
    kind = "unavailable"


class Internal(PixelError):
    """A real defect. The only kind worth a stack trace."""

    status = 500
    kind = "internal"


# Builtins that already mean something specific, so a handler can translate
# them without every raise site being converted first. This is the migration
# path rather than the destination: each entry that disappears from here is a
# module whose failures have been named.
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

    `ValueError` maps to 400 rather than 500 deliberately, even though it is a
    builtin and could be either. Of the 39 sites raising one, essentially all
    are rejecting input - and reporting bad input as a server fault is worse
    than reporting a rare internal ValueError as bad input. The named types
    exist so this guess stops being needed.
    """
    if isinstance(exc, PixelError):
        return exc.status
    for cls, status in BUILTIN_STATUS.items():
        if isinstance(exc, cls):
            return status
    return 500


def body_for(exc: BaseException) -> dict:
    """The JSON a failure becomes.

    A PixelError knows how to describe itself. Anything else gets its class
    name, because an unexpected exception's type is the most useful thing about
    it - and seeing `ValueError:` in a response is the signal that a raise site
    still needs naming.
    """
    if isinstance(exc, PixelError):
        return exc.as_dict()
    return {"error": f"{type(exc).__name__}: {exc}", "kind": "internal"}
