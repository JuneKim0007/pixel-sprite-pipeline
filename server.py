#!/usr/bin/env python3
"""Local web UI for the sprite pipeline.

    python server.py            # http://127.0.0.1:8000

Why a server and not a static page: the browser cannot read your directories,
edit config files, or start a run. Those are the whole point of the interface,
so there is a small backend - stdlib plus the YAML libraries already in use, no
framework.

What is left in this file is HTTP and only HTTP. Everything that decides what a
request *means* lives in pipeline/api/, as functions taking a Request and
returning a dict, so a handler can be exercised without opening a socket.
Dispatch is a table lookup; it used to be three if-chains, 125 lines for GET
alone, and every backend feature landed in the middle of one.

Binds to loopback only. Nothing here is authenticated, so do not expose it.
"""

from __future__ import annotations

import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Before anything imports numpy: the BLAS thread pools read their environment
# once, at load. This caps the SERVER, whose only CPU-heavy work is the editor.
# A generation run is a separate process and is deliberately left uncapped.
from pipeline.shared import limits  # noqa: E402

limits.apply()

from pipeline import api  # noqa: E402
from pipeline import files as files_mod  # noqa: E402
from pipeline.api.context import STATIC, allowed_roots, input_dir, runs_dir  # noqa: E402
from pipeline.shared import errors  # noqa: E402
from pipeline.stage import available  # noqa: E402


# --------------------------------------------------------------------- helpers


# ------------------------------------------------------------------ run state


# ------------------------------------------------------------------ editor
#
# The interactive half of the pixelisation stage. Everything here already
# existed as pipeline code and as CLI flags; what was missing was a way to see
# the effect of a choice before committing a night's GPU time to it.
#
# One thing this does that the usual converters do not: block size and grid
# phase are MEASURED rather than guessed with a slider. Both are recoverable
# from the image — the block size is the largest factor that reduces without
# loss, the phase is the offset whose blocks are most internally uniform — so
# offering a slider and no ruler would be withholding an answer we already
# have.


# ------------------------------------------------------------------- queue
# ------------------------------------------------------------------- handler


class Handler(BaseHTTPRequestHandler):
    """HTTP, and nothing else.

    Everything that decides what a request means now lives in pipeline/api/,
    reachable as functions. What is left here is the part that genuinely needs
    a socket: reading a body, streaming a file, parsing a multipart upload, and
    turning a failure into a status.

    Dispatch is a table lookup. It used to be three if-chains, 125 lines for
    GET alone, and every backend feature landed in the middle of one.
    """

    def log_message(self, fmt, *args):  # quieter console
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data).encode(), "application/json")

    def _error(self, code: int, msg: str) -> None:
        self._json({"error": msg}, code)

    def _fail(self, exc: BaseException) -> None:
        """One place that turns a failure into a response.

        There were three copies of the same catch-and-guess, and the last
        clause of each was `except Exception -> 500`, which reported a person
        typing nothing into a box as a server fault:

            POST /api/style/note {"text": "  "}
            500  {"error": "ValueError: a note needs some text"}

        The status now belongs to the exception. A PixelError describes itself,
        including a hint where there is a useful one; anything else is
        translated by type, and a body still carrying a bare class name is the
        signal that a raise site has not been named yet.
        """
        self._json(errors.body_for(exc), errors.status_for(exc))

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _upload(self) -> None:
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return self._error(400, "expected multipart/form-data")

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return self._error(400, "empty upload")
        if length > 256 * 1024 * 1024:
            return self._error(413, "upload too large")

        parts = files_mod.parse_multipart(self.rfile.read(length), ctype)
        target = input_dir()
        saved = []
        for _, filename, data in parts:
            if not filename or not data:
                continue
            dst = files_mod.unique_name(target, files_mod.safe_filename(filename))
            dst.write_bytes(data)
            saved.append({"name": dst.name, "path": str(dst)})

        if not saved:
            return self._error(400, "no files in the upload")
        return self._json({"saved": saved, "dir": str(target)})

    def _static(self, rel: str) -> None:
        p = (STATIC / rel).resolve()
        if not str(p).startswith(str(STATIC)) or not p.is_file():
            raise FileNotFoundError(rel)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if p.suffix == ".js":
            ctype = "text/javascript"
        self._send(200, p.read_bytes(), ctype)

    def _file(self, rel: str) -> None:
        p = files_mod.safe_path(rel, allowed_roots())
        if not p.is_file():
            raise FileNotFoundError(rel)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        self._send(200, p.read_bytes(), ctype)

    # -- dispatch

    def _serve(self, method: str) -> None:
        u = urlparse(self.path)
        try:
            if method == "GET" and (u.path in ("/", "/index.html")):
                return self._static("index.html")
            if method == "GET" and u.path.startswith("/static/"):
                return self._static(u.path[len("/static/"):])

            route = api.table.find(method, u.path)
            params = parse_qs(u.query)

            # The two routes that need the socket rather than a dict.
            if route.raw:
                if u.path == "/api/file":
                    return self._file((params.get("path") or [""])[0])
                if u.path == "/api/upload":
                    return self._json(self._upload())
                raise errors.Internal(f"no raw handler for {u.path}")

            body = self._body() if method in ("POST", "PUT") else {}
            req = api.Request(method=method, path=u.path, params=params, body=body)
            return self._json(route.handler(req))
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def do_GET(self) -> None:
        self._serve("GET")

    def do_POST(self) -> None:
        self._serve("POST")

    def do_PUT(self) -> None:
        self._serve("PUT")


def main() -> int:
    port = int(os.environ.get("PORT", 8000))
    STATIC.mkdir(exist_ok=True)
    runs_dir()
    input_dir()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"sprite pipeline UI -> http://127.0.0.1:{port}")
    print(f"  stages: {', '.join(sorted(available()))}")
    print(f"  inputs: {input_dir()}")
    print(f"  runs:   {runs_dir()}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
