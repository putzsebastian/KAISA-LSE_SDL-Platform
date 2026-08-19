"""A stand-in for the lab's ÄKTA control server, serving the published reference chromatogram.

Same rationale as device_stub.py: a generated ÄKTA analysis script fetches its chromatogram from
the control server, which is correct behaviour and is what the agent is told to write. Off the KIT
network that fetch fails and the script skips the analysis, so scoring it down would measure our
network rather than the agent.

Contract. The reference script uses:

    GET /api/akta/results/{experiment_id}   -> 200 {"results": {...}} | 404

and sends `X-API-Key`, which is accepted and ignored. Two further shapes a script might reasonably
reach for are also served, because the point is to exercise the analysis logic rather than to test
whether the agent guessed one exact spelling:

    GET /api/akta/data/{experiment_id}      -> the same payload
    GET /api/akta/results/{experiment_id}/info -> {"available": true, ...}

Every path requested is recorded; anything outside these is recorded separately as an undefined
endpoint rather than silently satisfied, so a script inventing routes stays visible.

The payload is the published `akta_results_<id>.json`, whose top level is the results object itself
(`signals`, `sample_time`, `uv1`, `cond`, `time`), so it is wrapped in {"results": ...} to match
what the real server returns and what the reference script unwraps with `data.get('results', {})`.
"""
from __future__ import annotations

import json
import pathlib
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _Handler(BaseHTTPRequestHandler):
    payload: bytes = b""
    available: bool = False
    experiment_id: str = ""
    hits: list = []
    unknown: list = []

    def _send(self, code: int, body: bytes, mime: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        _Handler.hits.append(self.path)

        m = re.match(r"^/api/akta/(?:results|data)/([^/]+)(/.*)?$", path)
        if not m:
            _Handler.unknown.append(self.path)
            self._send(404, b'{"error":"unknown endpoint"}')
            return
        tail = m.group(2) or ""

        if not _Handler.available:
            self._send(404, json.dumps({"available": False, "error": "No results found"}).encode())
            return

        if tail in ("/info", "/status"):
            self._send(200, json.dumps({"available": True, "experiment_id":
                                        _Handler.experiment_id}).encode())
            return
        if tail == "":
            self._send(200, _Handler.payload)
            return

        _Handler.unknown.append(self.path)
        self._send(404, b'{"error":"unknown endpoint"}')

    def log_message(self, *_a):  # keep the comparator's output clean
        return


class AktaResultsStub:
    """Serves one reference chromatogram over the ÄKTA control-server contract."""

    def __init__(self, results_json: pathlib.Path, experiment_id: str, port: int = 8100):
        self.results_json = pathlib.Path(results_json)
        self.experiment_id = str(experiment_id)
        self.port = port
        self._srv = None
        self._thread = None

    def __enter__(self):
        raw = (json.loads(self.results_json.read_text(encoding="utf-8"))
               if self.results_json.exists() else None)
        # tolerate a file that is already wrapped
        results = raw.get("results") if isinstance(raw, dict) and "results" in raw else raw
        _Handler.payload = json.dumps({"results": results}).encode()
        _Handler.available = results is not None
        _Handler.experiment_id = self.experiment_id
        _Handler.hits = []
        _Handler.unknown = []
        self._srv = HTTPServer(("127.0.0.1", self.port), _Handler)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
        return False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def hits(self) -> list:
        return list(_Handler.hits)

    @property
    def unknown(self) -> list:
        return list(_Handler.unknown)


def env_for(url: str) -> dict:
    """Environment the generated script reads to find the control server."""
    return {"AKTA_CONTROL_SERVER": url, "AKTA_API_KEY": "akta-control-key"}
