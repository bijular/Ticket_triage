"""Shared HTTP plumbing: JSON responses, scoped-token auth, per-run request logging."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

from .. import common


class JSONHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "triage-eval-mock/1.0"
    SCOPE = "read"
    LOG_NAME = "api_requests.jsonl"
    _lock = threading.Lock()

    def log_message(self, *a):  # silence default stderr logging; we keep our own run log
        pass

    # ---- helpers -----------------------------------------------------------
    def _send(self, status, body, headers=None):
        raw = json.dumps(body, sort_keys=True).encode()
        self._status, self._bytes = status, len(raw)
        self._log(getattr(self, "_claims_obj", None), getattr(self, "_log_extra", None))   # log before the client can see the reply
        self._logged = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _claims(self):
        h = self.headers.get("Authorization", "")
        if not h.startswith("Bearer "):
            raise common.AuthError("missing bearer token")
        return common.verify_token(h[7:].strip(), self.SCOPE)

    def _log(self, claims, extra=None):
        run = (claims or {}).get("run", "unauthenticated")
        d = common.RUNS_DIR_OVERRIDE if hasattr(common, "RUNS_DIR_OVERRIDE") else common.RUNS
        rec = {"ts": time.time(), "method": self.command, "path": self.url.path, "query": self.url.query,
               "status": getattr(self, "_status", 0), "bytes": getattr(self, "_bytes", 0),
               "tool": (claims or {}).get("tool"), "run": run}
        rec.update(extra or {})
        with self._lock:
            (d / run).mkdir(parents=True, exist_ok=True)
            with open(d / run / self.LOG_NAME, "a") as f:
                f.write(json.dumps(rec, sort_keys=True) + "\n")

    def _params(self):
        return {k: v[-1] for k, v in parse_qs(self.url.query, keep_blank_values=True).items()}

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        return json.loads(raw) if raw else {}

    def _dispatch(self):
        self.url = urlsplit(self.path)
        self._status, self._logged, self._claims_obj = 0, False, None
        claims = None
        try:
            if self.url.path == "/v1/health":
                return self._send(200, {"status": "ok"})
            try:
                claims = self._claims()
            except common.AuthError as e:
                self._send(401, {"error": f"unauthorized: {e}"})
                return
            self._claims_obj = claims
            self.route(claims)
        except Exception as e:  # never crash the server on a bad request
            self._send(500, {"error": f"internal error: {type(e).__name__}: {e}"})
        finally:
            if not self._logged:
                self._log(claims, getattr(self, "_log_extra", None))

    def do_GET(self):
        self._dispatch()

    do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = do_GET


def parse_ts(s):
    """ISO8601 -> aware UTC datetime; naive input is treated as UTC."""
    import datetime as dt
    s = s.strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    t = dt.datetime.fromisoformat(s)
    return t.replace(tzinfo=dt.timezone.utc) if t.tzinfo is None else t.astimezone(dt.timezone.utc)
