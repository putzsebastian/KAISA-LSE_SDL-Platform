"""One callback listener shared by every replicate, demultiplexed by job_id.

The device-protocol agent answers its webhook immediately and delivers the artifact later by
POSTing to `callback_url`. M2 gave each run its own listener on a fixed port, which is fine one run
at a time but collides the moment replicates run in parallel. Since `job_id` is already unique per
agent invocation, a single listener can route callbacks to whichever replicate is waiting.

Callbacks for a job that nobody is waiting on yet are kept, so a fast agent that calls back before
the caller starts waiting is not lost.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _Handler(BaseHTTPRequestHandler):
    hub = None

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"_unparsed": raw}
        _Handler.hub._deliver(body)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"success": True}).encode())

    def log_message(self, *a):
        pass


class CallbackHub:
    def __init__(self, host: str = "0.0.0.0", port: int = 8765):
        self.port = port
        self._lock = threading.Lock()
        self._received: dict[str, dict] = {}
        self._events: dict[str, threading.Event] = {}
        self._orphans: list[dict] = []
        _Handler.hub = self
        self.srv = HTTPServer((host, port), _Handler)
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def _deliver(self, body: dict):
        job_id = str(body.get("job_id") or "")
        stamped = {"received_at": dt.datetime.now(dt.timezone.utc).isoformat(), "body": body}
        with self._lock:
            if not job_id:
                self._orphans.append(stamped)
                return
            self._received[job_id] = stamped
            self._events.setdefault(job_id, threading.Event()).set()

    def expect(self, job_id: str):
        """Register interest before POSTing, so a fast callback is never missed."""
        with self._lock:
            self._events.setdefault(job_id, threading.Event())

    def wait(self, job_id: str, timeout: float):
        with self._lock:
            event = self._events.setdefault(job_id, threading.Event())
        if not event.wait(timeout):
            return None
        with self._lock:
            return self._received.get(job_id)

    def release(self, job_id: str):
        with self._lock:
            self._received.pop(job_id, None)
            self._events.pop(job_id, None)

    @property
    def orphans(self):
        with self._lock:
            return list(self._orphans)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()
