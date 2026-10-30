"""Read-only telemetry replay API. GET only; everything else is rejected with 405 and logged as a
write attempt. One server process serves every dataset; the dataset is chosen by the scoped token."""
import base64
import datetime as dt
import gzip
import hashlib
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from .. import common
from ..gen.core import LEVELS
from .base import JSONHandler, parse_ts


class Replay:
    """Loads and caches one dataset directory."""
    def __init__(self, d: Path):
        self.d = d
        rd = lambda n: json.loads((d / n).read_text())
        self.logs = [json.loads(l) for l in gzip.open(d / "logs.jsonl.gz", "rt") if l.strip()]
        self.traces = [json.loads(l) for l in gzip.open(d / "traces.jsonl.gz", "rt") if l.strip()]
        m = json.loads(gzip.open(d / "metrics.json.gz", "rt").read())
        self.metrics, self.metrics_start = m["series"], parse_ts(m["start"])
        self.deploys, self.alert, self.services = rd("deploys.json"), rd("alert.json"), rd("services.json")
        self.slow, self.explain, self.schema = rd("db/slow_queries.json"), rd("db/explain.json"), rd("db/schema.json")
        self.behavior = rd("behavior.json")
        self.jira = {p.stem: json.loads(p.read_text()) for p in (d / "jira").glob("*.json")} if (d / "jira").exists() else {}
        self.runbooks = {p.stem: p.read_text() for p in (d / "runbooks").glob("*.md")} if (d / "runbooks").exists() else {}
        for r in self.logs:   # parse once for fast filtering
            r["_t"] = parse_ts(r["TimeGenerated"])
            r["_lvl"] = LEVELS.index(r["SeverityLevel"])
            r["_hay"] = (r["Message"] + " " + json.dumps(r["Properties"])).lower()
        for r in self.traces:
            r["_t"] = parse_ts(r["TimeGenerated"])


class DataServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, handler, replay_root, flaky_rate=None, flaky_seed=7):
        super().__init__(addr, handler)
        self.replay_root = Path(replay_root)
        self.flaky_rate = common.CONFIG["flaky_rate"] if flaky_rate is None else flaky_rate
        self.flaky_seed = flaky_seed
        self._cache, self._seen, self._rule_counts = {}, set(), {}
        self._lock = threading.Lock()

    def replay(self, dsid):
        with self._lock:
            if dsid not in self._cache:
                self._cache[dsid] = Replay(self.replay_root / dsid)
            return self._cache[dsid]

    def flaky(self, run, key):
        """Deterministic: the *first* call for a given (run, request) may fail with 429/503; retries succeed."""
        if not self.flaky_rate:
            return None
        with self._lock:
            if (run, key) in self._seen:
                return None
            self._seen.add((run, key))
        h = int(hashlib.sha256(f"{self.flaky_seed}:{run}:{key}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        if h >= self.flaky_rate:
            return None
        return 429 if h < self.flaky_rate / 2 else 503

    def rule_hit(self, run, idx, times):
        with self._lock:
            n = self._rule_counts.get((run, idx), 0)
            if times is not None and n >= times:
                return False
            self._rule_counts[(run, idx)] = n + 1
            return True


def _ts(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


class Handler(JSONHandler):
    SCOPE = "read"
    LOG_NAME = "api_requests.jsonl"

    def route(self, claims):
        self._log_extra = {}
        if self.command != "GET":
            self._log_extra = {"write_attempt": True}
            return self._send(405, {"error": "read-only API: only GET is permitted"}, {"Allow": "GET"})
        p, q, srv = self.url.path, self._params(), self.server
        run = claims["run"]
        if p == "/v1/auth/renew":
            return self._send(200, {"token": common.mint_token(
                "read", run, claims["ds"], claims["tool"], max_exp=claims["max_exp"]),
                "expires_in_s": common.CONFIG["token_ttl_seconds"]})
        rp = srv.replay(claims["ds"])
        key = p + "?" + self.url.query
        if p not in ("/v1/alert",):
            f = srv.flaky(run, key)
            if f:
                self._log_extra = {"injected_fault": f}
                return self._send(f, {"error": "Too Many Requests" if f == 429 else "Service Unavailable",
                                      "retry_after_s": 1}, {"Retry-After": "1"})
        for i, rule in enumerate(rp.behavior.get("fail_rules", [])):
            if p == rule["path"] and rule["match"].lower() in self.url.query.lower() and srv.rule_hit(run, i, rule.get("times")):
                self._log_extra = {"injected_fault": rule["status"]}
                return self._send(rule["status"], rule["body"])
        fn = {"/v1/alert": self.alert, "/v1/services": self.services, "/v1/logs": self.logs,
              "/v1/traces": self.traces, "/v1/metrics": self.metrics, "/v1/metrics/names": self.metric_names,
              "/v1/deploys": self.deploys, "/v1/db/slow-queries": self.slow, "/v1/db/explain": self.explain,
              "/v1/db/schema": self.schema, "/v1/runbooks": self.runbooks_list}.get(p)
        if fn:
            return fn(rp, q)
        if p.startswith("/v1/jira/issues/"):
            k = p.rsplit("/", 1)[1]
            return self._send(200, rp.jira[k]) if k in rp.jira else self._send(404, {"error": "issue not found"})
        if p.startswith("/v1/runbooks/"):
            k = p.rsplit("/", 1)[1]
            return self._send(200, {"name": k, "markdown": rp.runbooks[k]}) if k in rp.runbooks \
                else self._send(404, {"error": "runbook not found"})
        self._send(404, {"error": "unknown endpoint", "endpoints": sorted(
            ["/v1/alert", "/v1/services", "/v1/logs", "/v1/traces", "/v1/metrics", "/v1/metrics/names", "/v1/deploys",
             "/v1/db/slow-queries", "/v1/db/explain", "/v1/db/schema", "/v1/jira/issues/{key}", "/v1/runbooks",
             "/v1/runbooks/{name}", "/v1/auth/renew"])})

    # ---- endpoints -------------------------------------------------------------
    def alert(self, rp, q):
        self._send(200, rp.alert)

    def services(self, rp, q):
        self._send(200, {"services": rp.services})

    def _window(self, q):
        try:
            s = parse_ts(q["start"]) if q.get("start") else None
            e = parse_ts(q["end"]) if q.get("end") else None
        except ValueError:
            raise ValueError("start/end must be ISO8601, e.g. 2026-03-12T02:00:00Z")
        return s, e

    def logs(self, rp, q):
        try:
            s, e = self._window(q)
        except ValueError as ex:
            return self._send(400, {"error": str(ex)})
        minlvl = LEVELS.index(q["severity"].capitalize()) if q.get("severity", "").capitalize() in LEVELS else 0
        svc, op, needle = q.get("service"), q.get("operation_Id"), q.get("q", "").lower()
        rows = [r for r in rp.logs
                if (not s or r["_t"] >= s) and (not e or r["_t"] < e) and r["_lvl"] >= minlvl
                and (not svc or r["AppRoleName"] == svc) and (not op or r["OperationId"] == op)
                and (not needle or needle in r["_hay"])]
        if q.get("order") == "desc":
            rows = rows[::-1]
        page_size = rp.behavior.get("logs_page_size", 1000)
        self._page_clean(rows, q, page_size, "AppTraces")

    def _page_clean(self, rows, q, page_size, name):
        # strip internal fields lazily (only the returned page)
        try:
            limit = min(int(q.get("limit", page_size)), page_size)
            off = json.loads(base64.urlsafe_b64decode(q["page_token"] + "==")).get("o", 0) if q.get("page_token") else 0
        except Exception:
            return self._send(400, {"error": "bad limit or page_token"})
        page = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows[off:off + limit]]
        nxt = None
        if off + limit < len(rows):
            nxt = base64.urlsafe_b64encode(json.dumps({"o": off + limit}).encode()).decode().rstrip("=")
        self._send(200, {"table": name, "count": len(page), "rows": page, "nextPageToken": nxt,
                         "maxPageSize": page_size})

    def traces(self, rp, q):
        try:
            s, e = self._window(q)
        except ValueError as ex:
            return self._send(400, {"error": str(ex)})
        svc, tgt, op, needle = q.get("service"), q.get("target"), q.get("operation_Id"), q.get("q", "").lower()
        rows = [r for r in rp.traces
                if (not s or r["_t"] >= s) and (not e or r["_t"] < e) and (not svc or r["AppRoleName"] == svc)
                and (not tgt or r["Target"] == tgt) and (not op or r["OperationId"] == op)
                and (not needle or needle in r["Name"].lower())]
        if q.get("failed") in ("1", "true"):
            rows = [r for r in rows if not r["Success"]]
        self._page_clean(rows, q, 1000, "AppDependencies")

    def metric_names(self, rp, q):
        self._send(200, {"metrics": sorted(rp.metrics)})

    def metrics(self, rp, q):
        name = q.get("metric")
        svc = q.get("service")
        want = f"{svc}/{name}" if svc and name else None
        if want not in rp.metrics:
            return self._send(404, {"error": "unknown service/metric; see /v1/metrics/names"})
        try:
            s, e = self._window(q)
        except ValueError as ex:
            return self._send(400, {"error": str(ex)})
        step = {"PT1M": 1, "PT5M": 5, "PT15M": 15, "PT1H": 60}.get(q.get("interval", "PT1M"))
        if not step:
            return self._send(400, {"error": "interval must be PT1M, PT5M, PT15M or PT1H"})
        cut = rp.behavior.get("metrics_empty_after")
        cut = parse_ts(cut) if cut else None
        vals, pts = rp.metrics[want], []
        buckets = {}
        for i, v in enumerate(vals):
            t = rp.metrics_start + dt.timedelta(minutes=i)
            if (s and t < s) or (e and t >= e) or (cut and t >= cut):
                continue
            b = t - dt.timedelta(minutes=t.minute % step if step < 60 else t.minute)
            buckets.setdefault(b, []).append(v)
        for b in sorted(buckets):
            xs = buckets[b]
            pts.append([_ts(b), round(sum(xs) / len(xs), 3)])
        self._send(200, {"service": svc, "metric": name, "interval": q.get("interval", "PT1M"),
                         "aggregation": "mean", "points": pts})

    def deploys(self, rp, q):
        rows = [d for d in rp.deploys if not q.get("service") or d["service"] == q["service"]]
        self._send(200, {"deployments": rows})

    def slow(self, rp, q):
        if q.get("snapshot"):
            hit = [x for x in rp.slow if x["snapshot"] == q["snapshot"]]
            return self._send(200, hit[0]) if hit else self._send(404, {"error": "no such snapshot"})
        self._send(200, {"snapshots": [x["snapshot"] for x in rp.slow], "latest": rp.slow[-1] if rp.slow else None})

    def explain(self, rp, q):
        qid, snap = q.get("query_id"), q.get("snapshot", "latest")
        plan = rp.explain.get(f"{qid}@{snap}")
        if not plan:
            return self._send(404, {"error": "no EXPLAIN ANALYZE captured for that query_id/snapshot (try snapshot=first|latest)",
                                    "available": sorted(rp.explain)})
        self._send(200, {"query_id": qid, "snapshot": snap, "plan": plan})

    def schema(self, rp, q):
        self._send(200, rp.schema)

    def runbooks_list(self, rp, q):
        self._send(200, {"runbooks": sorted(rp.runbooks)})


def make_server(host="127.0.0.1", port=8081, replay_root=None, **kw):
    return DataServer((host, port), Handler, replay_root or common.REPLAY, **kw)


def serve(host, port, replay_root=None, **kw):
    srv = make_server(host, port, replay_root, **kw)
    print(f"data API on http://{host}:{port}  (GET only)  replay={srv.replay_root}")
    srv.serve_forever()
