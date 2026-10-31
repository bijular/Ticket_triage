"""Sandbox ticket system (Jira/ADO-like). The only write path in the whole kit. Idempotent on
(run, dedupe_key), rate-limited per run, and every request is logged."""
import json
import re
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

    @staticmethod
    def _ado_body(patch):
        """Azure DevOps JSON-patch [{op,path:/fields/X,value}] -> internal ticket fields."""
        f = {}
        for op in patch if isinstance(patch, list) else []:
            if op.get("op") in ("add", "replace") and str(op.get("path", "")).startswith("/fields/"):
                f[op["path"][8:]] = op.get("value")
        tags = [x.strip() for x in str(f.get("System.Tags", "")).split(";") if x.strip()]
        dk = next((x[7:] for x in tags if x.startswith("dedupe:")), None)
        return {"title": f.get("System.Title"), "description": f.get("System.Description", ""), "dedupe_key": dk,
                "severity": str(f.get("Microsoft.VSTS.Common.Severity", "3 - Medium")),
                "labels": [x for x in tags if not x.startswith("dedupe:")],
                "evidence_refs": re.findall(r"\b(?:log-\d{7}|dep-\d{6}|deploy-\d{4})\b", str(f.get("System.Description", ""))),
                "proposed_remediation": str(f.get("Custom.ProposedRemediation", ""))}

    @staticmethod
    def _ado_view(t):
        i = int(t["key"].split("-")[1])
        return {"id": i, "rev": 1, "fields": {"System.WorkItemType": "Bug", "System.Title": t["title"],
                "System.Description": t["description"], "System.State": "New",
                "System.Tags": "; ".join(t["labels"] + [f"dedupe:{t['dedupe_key']}"])},
                "url": f"https://dev.azure.example/acme/sandbox/_apis/wit/workitems/{i}"}

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
            if self.command == "POST" and (p == "/v1/tickets" or "/_apis/wit/workitems/" in p):
                ado = p != "/v1/tickets"
                body = self._ado_body(self._body()) if ado else self._body()
                if not body.get("title") or not body.get("dedupe_key"):
                    return self._send(400, {"error": "title and dedupe_key are required" + (
                        " (ADO: add a System.Tags entry 'dedupe:<key>')" if ado else "")})
                for t in mine:
                    if t["dedupe_key"] == body["dedupe_key"]:
                        self._log_extra = {"duplicate": True}
                        return self._send(200, {**(self._ado_view(t) if ado else t), "duplicate": True})
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
                     "dedupe_key": body["dedupe_key"], "comments": [], "created": now, "status": "Open",
                     "via": "ado" if ado else "rest"}
                mine.append(t)
                self._persist(run, {"event": "created", "ticket": t})
                return self._send(201, {**(self._ado_view(t) if ado else t), "duplicate": False})
            if self.command == "GET" and "/_apis/wit/workitems/" in p:
                wid = p.rsplit("/", 1)[1]
                for t in mine:
                    if str(int(t["key"].split("-")[1])) == wid:
                        return self._send(200, self._ado_view(t))
                return self._send(404, {"message": f"work item {wid} not found"})
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
