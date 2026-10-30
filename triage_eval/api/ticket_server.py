"""Sandbox ticket system (Jira/ADO-like). The only write path in the whole kit. Idempotent on
(run, dedupe_key), rate-limited per run, and every request is logged."""
import json
import threading
import time
from http.server import ThreadingHTTPServer

from .. import common
from .base import JSONHandler


class TicketServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, handler, limits=None):
        super().__init__(addr, handler)
        self.limits = limits or common.CONFIG["ticket_limits"]
        self.tickets = {}      # run -> list of ticket dicts
        self.stamps = {}       # run -> recent create timestamps
        self.lock = threading.Lock()


class Handler(JSONHandler):
    SCOPE = "tickets"
    LOG_NAME = "ticket_requests.jsonl"

    def _persist(self, run, event):
        d = (common.RUNS_DIR_OVERRIDE if hasattr(common, "RUNS_DIR_OVERRIDE") else common.RUNS) / run
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "tickets.jsonl", "a") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")

    def _load(self, run):
        d = (common.RUNS_DIR_OVERRIDE if hasattr(common, "RUNS_DIR_OVERRIDE") else common.RUNS) / run / "tickets.jsonl"
        out = []
        if d.exists():
            for line in d.read_text().splitlines():
                ev = json.loads(line)
                if ev["event"] == "created":
                    out.append(ev["ticket"])
                elif ev["event"] == "commented":
                    for t in out:
                        if t["key"] == ev["key"]:
                            t["comments"].append(ev["comment"])
        return out

    def route(self, claims):
        srv, run, p = self.server, claims["run"], self.url.path
        self._log_extra = {}
        if p == "/v1/auth/renew" and self.command == "GET":
            return self._send(200, {"token": common.mint_token("tickets", run, claims["ds"], claims["tool"],
                                                              max_exp=claims["max_exp"])})
        with srv.lock:
            if run not in srv.tickets:
                srv.tickets[run] = self._load(run)      # survive restarts: dedupe still holds on a repeated run
            mine = srv.tickets[run]
            if p == "/v1/tickets" and self.command == "POST":
                body = self._body()
                if not body.get("title") or not body.get("dedupe_key"):
                    return self._send(400, {"error": "title and dedupe_key are required"})
                for t in mine:
                    if t["dedupe_key"] == body["dedupe_key"]:
                        self._log_extra = {"duplicate": True}
                        return self._send(200, {**t, "duplicate": True})
                now = time.time()
                recent = [x for x in srv.stamps.setdefault(run, []) if now - x < 60]
                srv.stamps[run] = recent
                if len(recent) >= srv.limits["per_minute"] or len(mine) >= srv.limits["per_run"]:
                    self._log_extra = {"rate_limited": True}
                    return self._send(429, {"error": "ticket rate limit exceeded", "retry_after_s": 30},
                                      {"Retry-After": "30"})
                recent.append(now)
                t = {"key": f"INC-{len(mine) + 1:03d}", "title": str(body["title"]), "description": str(body.get("description", "")),
                     "severity": body.get("severity", "Sev3"), "labels": body.get("labels", []),
                     "evidence_refs": body.get("evidence_refs", []),
                     "proposed_remediation": str(body.get("proposed_remediation", "")),
                     "dedupe_key": body["dedupe_key"], "comments": [], "created": now, "status": "Open"}
                mine.append(t)
                self._persist(run, {"event": "created", "ticket": t})
                return self._send(201, {**t, "duplicate": False})
            if p == "/v1/tickets" and self.command == "GET":
                dk = self._params().get("dedupe_key")
                return self._send(200, {"tickets": [t for t in mine if not dk or t["dedupe_key"] == dk]})
            if p.startswith("/v1/tickets/") and p.endswith("/comments") and self.command == "POST":
                key = p.split("/")[3]
                for t in mine:
                    if t["key"] == key:
                        c = {"body": str(self._body().get("body", "")), "created": time.time()}
                        t["comments"].append(c)
                        self._persist(run, {"event": "commented", "key": key, "comment": c})
                        return self._send(201, c)
                return self._send(404, {"error": "no such ticket in this sandbox project"})
            if p.startswith("/v1/tickets/") and self.command == "GET":
                key = p.rsplit("/", 1)[1]
                for t in mine:
                    if t["key"] == key:
                        return self._send(200, t)
                return self._send(404, {"error": "no such ticket in this sandbox project"})
        self._send(404, {"error": "unknown endpoint; supported: POST/GET /v1/tickets, GET /v1/tickets/{key}, "
                                  "POST /v1/tickets/{key}/comments"})


def make_server(host="127.0.0.1", port=8082, limits=None):
    return TicketServer((host, port), Handler, limits)


def serve(host, port):
    print(f"ticket sandbox on http://{host}:{port}")
    make_server(host, port).serve_forever()
