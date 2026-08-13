
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


from pipeline.shared import limits  # noqa: E402

limits.apply()

from pipeline import api  # noqa: E402
from pipeline.shared import files as files_mod  # noqa: E402
from pipeline.api.context import STATIC, allowed_roots, input_dir, runs_dir  # noqa: E402
from pipeline.shared import errors  # noqa: E402
from pipeline.generation.stage import available  # noqa: E402


class Handler(BaseHTTPRequestHandler):


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
