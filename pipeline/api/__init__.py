"""The HTTP surface, as functions rather than as a request handler.

Every route is `(Request) -> dict`. Nothing here touches a socket, so a handler
can be called from a test or a CLI without starting anything, and the route
table can be enumerated - which is what lets the surface be checked rather than
only served.

A domain module is mounted by being imported: subclassing BaseRouter registers
it. The imports below are that mounting, and they are the only list of what the
API consists of.

server.py keeps the parts that genuinely are HTTP: sockets, headers, static
files, multipart uploads, and turning an exception into a status.
"""

from .routing import BaseRouter, Request, Route, table

from . import (configs, editor, files, jobs, looks, machine, poses,  # noqa: F401
               runs)  # noqa: F401,E402

__all__ = ["BaseRouter", "Request", "Route", "table"]
