from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from threading import Thread

import pytest


@pytest.fixture(scope="session")
def host():
    from server import Handler

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()
        srv.server_close()


def _honours_contract(method: str, path: str, body):
    """Every successful call any test makes is a contract check, so the POST and PUT routes are covered by whatever already exercises them rather than by a second suite that would have to fake their side effects."""
    from pipeline import api

    route = {(r["method"], r["path"]): r
             for r in api.table.surface()}.get((method, path.split("?")[0]))
    if route is None or route["returns"] is None:
        return
    faults = route["returns"].check(body)
    assert not faults, (f"{method} {path} declares {route['returns']} but "
                        + "; ".join(faults))


@pytest.fixture(scope="session")
def http(host):
    class Client:
        def get(self, path):
            return self.raw(path)

        def raw(self, path):
            """The body as sent: bytes for a file route, parsed for the rest."""
            with urllib.request.urlopen(host + path, timeout=20) as r:
                body = r.read()
                if "json" in r.headers.get("Content-Type", ""):
                    body = json.loads(body)
            _honours_contract("GET", path, body)
            return body

        def send(self, path, payload, method="POST"):
            req = urllib.request.Request(
                host + path, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method=method)
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read())
            _honours_contract(method, path, body)
            return body

        def status(self, path, payload=None, method="GET"):
            try:
                if payload is None:
                    urllib.request.urlopen(host + path, timeout=20)
                else:
                    self.send(path, payload, method)
                return 200
            except urllib.error.HTTPError as e:
                return e.code

        def failure(self, path, payload=None, method="GET"):
            try:
                if payload is None:
                    urllib.request.urlopen(host + path, timeout=20)
                else:
                    self.send(path, payload, method)
                return 200, {}
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")

    return Client()
