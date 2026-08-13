
from __future__ import annotations

import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

MAX_BODY = 256 * 1024 * 1024

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


from pipeline.shared import limits  # noqa: E402

limits.apply()

from pipeline import api  # noqa: E402
from pipeline.api.context import STATIC, input_dir, runs_dir  # noqa: E402
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

    def _static(self, rel: str) -> None:
        p = (STATIC / rel).resolve()
        if not str(p).startswith(str(STATIC)) or not p.is_file():
            raise FileNotFoundError(rel)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if p.suffix == ".js":
            ctype = "text/javascript"
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
            ctype = self.headers.get("Content-Type", "")
            raw_body, body = b"", {}
            if method in ("POST", "PUT"):
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    return self._error(413, "request body too large")
                raw_body = self.rfile.read(length) if length > 0 else b""
                if not ctype or "json" in ctype:
                    body = json.loads(raw_body or b"{}")

            result = route.handler(api.Request(
                method=method, path=u.path, params=parse_qs(u.query),
                body=body, raw_body=raw_body, content_type=ctype,
            ))
            if isinstance(result, api.Raw):
                return self._send(200, result.body, result.content_type)
            return self._json(result)
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
