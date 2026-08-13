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


@pytest.fixture(scope="session")
def http(host):
    class Client:
        def get(self, path):
            with urllib.request.urlopen(host + path, timeout=20) as r:
                return json.loads(r.read())

        def send(self, path, payload, method="POST"):
            req = urllib.request.Request(
                host + path, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method=method)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())

        def status(self, path, payload=None, method="GET"):
            try:
                if payload is None:
                    urllib.request.urlopen(host + path, timeout=20)
                else:
                    self.send(path, payload, method)
                return 200
            except urllib.error.HTTPError as e:
                return e.code

    return Client()
