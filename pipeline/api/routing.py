
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar

from ..shared.errors import Conflict, Invalid, NotFound
from ..shared.registry import Registry, Source
from .contracts import Shape


@dataclass
class Request:
    """Everything a handler is allowed to know about the call."""

    method: str
    path: str
    params: dict[str, list[str]] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)
    raw_body: bytes = b""
    content_type: str = ""

    def query(self, name: str, default: str = "") -> str:
        values = self.params.get(name) or []
        return values[0] if values and values[0] else default

    def required(self, name: str) -> str:
        """A missing parameter as a 400 rather than an empty string."""
        value = self.query(name)
        if not value:
            raise Invalid(f"'{name}' is required", field=name)
        return value

    def flag(self, name: str) -> bool:
        return self.query(name) in ("1", "true", "yes", "on")

    def get(self, name: str, default: Any = None) -> Any:
        """A body field, with a blank treated as absent."""
        value = self.body.get(name, None)
        return default if value is None else value

    def need(self, name: str) -> Any:
        value = self.body.get(name, None)
        if value is None or value == "":
            raise Invalid(f"'{name}' is required", field=name)
        return value


@dataclass
class Raw:
    """A handler's answer when it is bytes rather than JSON."""

    body: bytes
    content_type: str = "application/octet-stream"


@dataclass
class Route:
    method: str
    path: str
    handler: Callable[..., Any]
    summary: str = ""
    returns: Shape | None = None


def _mark(method: str, path: str, summary: str, returns: Shape | None):
    """Attach route metadata to a method; BaseRouter collects it."""
    if not isinstance(returns, Shape):
        raise Invalid(f"{method} {path} declares no response contract",
                      field=path,
                      hint="pass returns=Shape(...), or Bytes()/Anything() "
                           "when there is genuinely no object to promise")

    def wrap(fn):
        fn._route = (method, path, summary, returns)
        return fn
    return wrap


def get(path: str, summary: str = "", *, returns: Shape | None = None):
    return _mark("GET", path, summary, returns)


def post(path: str, summary: str = "", *, returns: Shape | None = None):
    return _mark("POST", path, summary, returns)


def put(path: str, summary: str = "", *, returns: Shape | None = None):
    return _mark("PUT", path, summary, returns)


class BaseRouter:
    """A group of routes."""

    prefix: ClassVar[str] = ""
    REGISTRY: ClassVar[list[type["BaseRouter"]]] = []

    def __init_subclass__(cls, **kw) -> None:
        super().__init_subclass__(**kw)
        BaseRouter.REGISTRY.append(cls)

    @classmethod
    def routes(cls) -> list[Route]:
        instance = cls()
        found: list[Route] = []
        for name in dir(cls):
            member = getattr(cls, name, None)
            meta = getattr(member, "_route", None)
            if meta is None:
                continue
            method, path, summary, returns = meta
            found.append(Route(method, cls.prefix + path,
                               getattr(instance, name), summary, returns))
        return found


class _Declared(Source[Route]):

    def signature(self) -> Any:
        return tuple(f"{c.__module__}.{c.__qualname__}"
                     for c in BaseRouter.REGISTRY)

    def load(self) -> tuple[dict[str, Route], list]:
        found: dict[str, Route] = {}
        for cls in BaseRouter.REGISTRY:
            for route in cls.routes():
                key = f"{route.method} {route.path}"
                if key in found:
                    raise Conflict(f"two routers both claim {key}")
                found[key] = route
        return found, []


class Table:

    def __init__(self) -> None:
        self._routes: Registry[Route] = Registry("route", _Declared())

    def find(self, method: str, path: str) -> Route:
        route = self._routes.find(f"{method} {path}")
        if route is not None:
            return route
        others = sorted({r.method for r in self._routes.all().values()
                         if r.path == path})
        if others:
            raise Invalid(f"{path} accepts {', '.join(others)}, not {method}")
        raise NotFound("route", path)

    def surface(self) -> list[dict]:
        """Every route, for listing and for checking against contracts."""
        return [{"method": r.method, "path": r.path, "summary": r.summary,
                 "returns": r.returns}
                for r in sorted(self._routes.all().values(),
                                key=lambda r: (r.path, r.method))]

    def __len__(self) -> int:
        return len(self._routes)


table = Table()
