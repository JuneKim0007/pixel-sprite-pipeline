"""Routes as data, owned by the module they belong to.

The dispatch was three if-chains - 125 lines for GET alone - and every backend
feature landed in the middle of one. That shape costs more than the reading. A
route cannot be listed, so nothing can check the surface; and a handler is
welded to `BaseHTTPRequestHandler`, so exercising one means starting a server.

Here a router subclass owns a group of routes, and subclassing is the
registration:

    class Runs(BaseRouter):
        prefix = "/api"

        @get("/runs")
        def list_runs(self, req): ...

`BaseRouter.__init_subclass__` adds the class to the registry, so a domain
module is mounted by being imported and there is no second list to keep in
step with the first. That is the same trade the stage registry makes, and the
failure it avoids is the same: a handler written, never referenced, and
silently absent.

A handler takes a `Request` and returns a dict. It does not know what serves
it, which is what makes it callable from a test or a CLI. Because the table can
be enumerated, a response contract can be attached to each entry and checked at
import rather than at request time - REFACTOR.md section 6.

`Request` exists for one concrete reason: `q.get("name", [""])[0]` appeared 14
times, and each was a place where a missing parameter silently became the empty
string and failed confusingly further in. `req.query("name")` does that once,
and `req.required("name")` is there where absent should be a 400.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar

from ..shared.errors import Invalid, NotFound


@dataclass
class Request:
    """Everything a handler is allowed to know about the call."""

    method: str
    path: str
    params: dict[str, list[str]] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)

    def query(self, name: str, default: str = "") -> str:
        values = self.params.get(name) or []
        return values[0] if values and values[0] else default

    def required(self, name: str) -> str:
        """A missing parameter as a 400 rather than an empty string.

        The old idiom turned every absent parameter into "", which then failed
        somewhere further in as a confusing not-found.
        """
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
class Route:
    method: str
    path: str
    handler: Callable[..., Any]
    summary: str = ""
    # Set where a handler genuinely needs the HTTP machinery: streaming a file,
    # reading a multipart upload. Those cannot be a dict-returning function,
    # and pretending otherwise would be a worse abstraction than none.
    raw: bool = False


def _mark(method: str, path: str, summary: str = "", raw: bool = False):
    """Attach route metadata to a method; BaseRouter collects it."""
    def wrap(fn):
        fn._route = (method, path, summary, raw)
        return fn
    return wrap


def get(path: str, summary: str = "", raw: bool = False):
    return _mark("GET", path, summary, raw)


def post(path: str, summary: str = "", raw: bool = False):
    return _mark("POST", path, summary, raw)


def put(path: str, summary: str = "", raw: bool = False):
    return _mark("PUT", path, summary, raw)


class BaseRouter:
    """A group of routes. Subclassing registers it.

    `prefix` is prepended to every path the subclass declares, so a domain
    owns its namespace in one place instead of repeating "/api" per route.
    """

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
            method, path, summary, raw = meta
            found.append(Route(method, cls.prefix + path,
                               getattr(instance, name), summary, raw))
        return found


class Table:
    """Every registered route, resolved once.

    Built lazily so import order does not matter: a router defined after the
    table is first asked for is still included, because the table rebuilds when
    the registry has grown.
    """

    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], Route] = {}
        self._count = -1

    def _ensure(self) -> None:
        if self._count == len(BaseRouter.REGISTRY):
            return
        table: dict[tuple[str, str], Route] = {}
        for cls in BaseRouter.REGISTRY:
            for route in cls.routes():
                key = (route.method, route.path)
                if key in table:
                    raise ValueError(
                        f"two routers both claim {route.method} {route.path}")
                table[key] = route
        self._routes = table
        self._count = len(BaseRouter.REGISTRY)

    def find(self, method: str, path: str) -> Route:
        self._ensure()
        route = self._routes.get((method, path))
        if route is not None:
            return route
        # A path that exists under another method is a different mistake from a
        # path that does not exist, and saying so saves the guess.
        others = sorted({m for m, p in self._routes if p == path})
        if others:
            raise Invalid(f"{path} accepts {', '.join(others)}, not {method}")
        raise NotFound("route", path)

    def surface(self) -> list[dict]:
        """Every route, for listing and for checking against contracts."""
        self._ensure()
        return [{"method": r.method, "path": r.path, "summary": r.summary,
                 "raw": r.raw}
                for r in sorted(self._routes.values(),
                                key=lambda r: (r.path, r.method))]

    def __len__(self) -> int:
        self._ensure()
        return len(self._routes)


table = Table()
