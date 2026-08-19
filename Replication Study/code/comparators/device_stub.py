"""A stand-in for the lab's device control server, serving the published reference data.

Why this exists. A freshly generated analysis script fetches its Tecan workbook from the device
control server — that is the correct behaviour, and it is what the agent is told to write. The
replication harness runs off the KIT network, so that fetch fails and the script falls back to
skipping the analysis. Scoring a replicate down for that would measure our network, not the agent.

So the harness serves the reference workbook over the same HTTP contract the lab server uses. The
script then takes its intended primary path and the comparator tests the analysis logic, which is
what the milestone asks for ("execute against the reference raw data").

Contract. All four routes below exist on the real device control server (confirmed with the lab),
so a script using any of them is behaving correctly - the published reference script happens to use
/info and the bare path, but /list and /file are equally valid:

    GET /api/tecan/data/{experiment_id}/info            -> 200 {"available": true, ...}  | 404
    GET /api/tecan/data/{experiment_id}/list            -> 200 {"total_files": N, ...}   | 404
    GET /api/tecan/data/{experiment_id}[?file_index=N]  -> 200 xlsx bytes                | 404
    GET /api/tecan/data/{experiment_id}/file/{filename} -> 200 xlsx bytes                | 404

The X-API-Key header is accepted and ignored. Which route a candidate actually took is recorded,
since that is itself an observation about how much generated scripts vary; anything OUTSIDE the
four routes is recorded separately as an undefined endpoint rather than silently satisfied.
"""
from __future__ import annotations

import json
import pathlib
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class _Handler(BaseHTTPRequestHandler):
    workbook: pathlib.Path = None
    hits: list = []
    unknown: list = []

    def _send(self, code: int, body: bytes, mime: str):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        _Handler.hits.append(self.path)
        wb = _Handler.workbook
        have = bool(wb and wb.exists())

        m = re.match(r"^/api/tecan/data/([^/]+)(/.*)?$", path)
        if not m:
            _Handler.unknown.append(self.path)
            self._send(404, b'{"error":"unknown endpoint"}', "application/json")
            return
        exp, tail = m.group(1), (m.group(2) or "")
        name = f"tecan_data_{exp}.xlsx"

        # Metadata. Both real shapes are served: /info as the reference script uses it, and /list
        # returning total_files + files. Every path requested is recorded either way, so a route
        # outside the contract stays visible rather than being silently satisfied.
        if tail in ("/info", "/list"):
            if not have:
                self._send(404, json.dumps({"available": False, "total_files": 0,
                                            "files": [], "error": "No data found"}).encode(),
                           "application/json")
                return
            entry = {"filename": name, "size": wb.stat().st_size, "experiment_id": exp}
            payload = ({"available": True, "total_files": 1, "files": [entry], **entry}
                       if tail == "/list" else {"available": True, **entry})
            self._send(200, json.dumps(payload).encode(), "application/json")
            return

        # File download: bare, ?file_index=N, or /file/<name>
        if tail == "" or tail.startswith("/file"):
            if have:
                self._send(200, wb.read_bytes(), XLSX_MIME)
            else:
                self._send(404, b'{"error":"not found"}', "application/json")
            return

        _Handler.unknown.append(self.path)
        self._send(404, b'{"error":"unknown endpoint"}', "application/json")

    def log_message(self, *a):  # keep the comparator's output readable
        pass


class DeviceDataStub:
    """Context manager: serves `workbook` on 127.0.0.1:`port` for the duration of the block."""

    def __init__(self, workbook: pathlib.Path, port: int = 8000):
        _Handler.workbook = pathlib.Path(workbook)
        _Handler.hits = []
        _Handler.unknown = []
        self.port = port
        self.srv = HTTPServer(("127.0.0.1", port), _Handler)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def hits(self) -> list:
        return list(_Handler.hits)

    @property
    def unknown(self) -> list:
        """Endpoints a candidate invented that the lab contract does not define."""
        return list(_Handler.unknown)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()


def env_for(url: str, api_key: str = "replication-harness") -> dict:
    """Every env var name the generated scripts have been observed to read."""
    return {
        "DEVICE_CONTROL_SERVER": url,
        "DEVICE_CONTROL_SERVER_LAB167": url,
        "DEVICE_CONTROL_SERVER_LAB168": url,
        "DEVICE_API_KEY": api_key,
        "DEVICE_API_KEY_LAB167": api_key,
        "DEVICE_API_KEY_LAB168": api_key,
    }


def classify_route(hits: list, results: dict) -> str:
    """How did the candidate actually obtain its data?"""
    source = str((results.get("data_outputs") or {}).get("tecan_data_source", ""))
    if any("/api/tecan/data/" in h for h in hits):
        return "device_server"
    if source == "embedded_json":
        return "embedded_json"
    if source:
        return "local_file"
    return "none"
