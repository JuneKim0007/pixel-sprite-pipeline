"""What went wrong, and whose fault it was."""

from __future__ import annotations


class PixelError(Exception):
    """A failure this system understands well enough to describe."""

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
    """A named thing does not exist, with the alternatives named."""

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
    """The request is refused because computing it would not fit."""

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
    """A dependency is not running."""

    status = 503
    kind = "unavailable"


class Internal(PixelError):
    """A real defect. The only kind worth a stack trace."""

    status = 500
    kind = "internal"


# Four of these seven have no raise site here; open(), os and urllib raise them for us.
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
    """ValueError maps to 400 rather than 500: of the 39 sites raising one, essentially all reject input, and reporting bad input as a server fault is worse than the reverse."""
    if isinstance(exc, PixelError):
        return exc.status
    for cls, status in BUILTIN_STATUS.items():
        if isinstance(exc, cls):
            return status
    return 500


def body_for(exc: BaseException) -> dict:
    if isinstance(exc, PixelError):
        return exc.as_dict()
    return {"error": f"{type(exc).__name__}: {exc}", "kind": "internal"}
