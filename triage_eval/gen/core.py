"""Synthetic telemetry core: baseline logs/traces/metrics plus helpers to plant evidence.

Everything is driven by a seeded ``random.Random`` so output is byte-identical per seed.
Record shapes mimic Application Insights / Log Analytics exports (AppTraces,
AppDependencies, metrics at 1-minute grain).
"""
import copy
import datetime as dt
import math
import random

UTC = dt.timezone.utc
START = dt.datetime(2026, 3, 11, 12, 0, tzinfo=UTC)
END = dt.datetime(2026, 3, 12, 3, 0, tzinfo=UTC)
N_MIN = int((END - START).total_seconds() // 60)
LEVELS = ["Verbose", "Information", "Warning", "Error", "Critical"]


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def T(h, m, s=0, day=12):
    """Time on the incident night (default 2026-03-12)."""
    return dt.datetime(2026, 3, day, h, m, s, tzinfo=UTC)


def minute_index(t):
    return int((t - START).total_seconds() // 60)


def rhythm(t):
    """Daily load rhythm, peak ~15:00 UTC, in [-1, 1]."""
    h = t.hour + t.minute / 60
    return math.sin((h - 9) / 24 * 2 * math.pi)


SERVICES = {
    "api-gateway": "Public ingress, authn/z, rate limiting",
    "document-processing": "Accepts documents, orchestrates OCR, indexing and persistence",
    "ocr-worker": "OCR/layout workers consuming the page queue",
    "nightly-batch": "Nightly re-processing job (starts 02:00 UTC, deadline 04:00 UTC)",
    "postgres": "PostgreSQL primary for documents and agent checkpoints",
    "agent-orchestrator": "LangGraph agents (triage-agent) with Postgres checkpointer",
    "vendor-client": "Outbound client for the docuvision extraction API",
    "auth-service": "Internal token issuance and JWKS",
    "notification-service": "Email/webhook notifications",
    "cert-refresher": "Sidecar that renews internal TLS certificates",
    "monitoring-agent": "Metrics exporter / histogram aggregation",
}

# service, metric, base, daily-amplitude, noise-fraction, decimals
BASE_METRICS = [
    ("document-processing", "p95_latency_ms", 2000, 0.08, 0.05, 0),
    ("document-processing", "request_rate_rpm", 600, 0.30, 0.05, 0),
    ("document-processing", "error_rate_pct", 0.4, 0.0, 0.20, 2),
    ("document-processing", "cpu_pct", 38, 0.15, 0.05, 1),
    ("document-processing", "worker_threads_active", 22, 0.20, 0.08, 0),
    ("document-processing", "cache_hit_ratio", 0.93, 0.0, 0.01, 3),
    ("api-gateway", "request_rate_rpm", 1500, 0.30, 0.05, 0),
    ("api-gateway", "p95_latency_ms", 240, 0.08, 0.06, 0),
    ("api-gateway", "http_5xx_rate_pct", 0.2, 0.0, 0.25, 2),
    ("ocr-worker", "memory_mb", 2300, 0.0, 0.02, 0),
    ("ocr-worker", "restarts_per_min", 0, 0.0, 0.0, 0),
    ("ocr-worker", "queue_depth", 120, 0.20, 0.15, 0),
    ("ocr-worker", "pages_per_min", 900, 0.25, 0.05, 0),
    ("nightly-batch", "projected_slack_min", 90, 0.0, 0.02, 0),
    ("postgres", "db_active_connections", 34, 0.20, 0.07, 0),
    ("postgres", "db_avg_query_ms", 38, 0.10, 0.08, 1),
    ("postgres", "db_cpu_pct", 32, 0.15, 0.06, 1),
    ("agent-orchestrator", "llm_tokens_per_min", 42000, 0.25, 0.08, 0),
    ("agent-orchestrator", "run_duration_p95_s", 3.1, 0.0, 0.05, 2),
    ("agent-orchestrator", "recursion_limit_hits_per_min", 0, 0.0, 0.0, 0),
    ("vendor-client", "outbound_requests_per_min", 420, 0.25, 0.05, 0),
    ("vendor-client", "vendor_error_rate_pct", 0.1, 0.0, 0.30, 2),
    ("vendor-client", "vendor_p95_ms", 820, 0.0, 0.06, 0),
    ("monitoring-agent", "scrape_success_ratio", 1.0, 0.0, 0.0, 3),
]

# ---- benign log templates ---------------------------------------------------
_DOC_NOUN = ["invoice", "contract", "statement", "form", "report"]


def _tenant(r):
    return f"tnt-{r.randint(1, 500):04d}"


def _doc(r):
    return f"doc-{r.getrandbits(40):010x}"


def _benign_templates():
    """(service, weight, level, fn(rng, ds, t) -> (message, props))."""
    def lat(ds, t, mean, sd, r):
        return max(40, int(r.gauss(mean, sd) * ds.lat_fn(t)))

    return [
        ("api-gateway", 14, 1, lambda r, ds, t: (
            f"GET /v1/documents/{_doc(r)} 200 {lat(ds, t, 85, 25, r)}ms", {"tenant": _tenant(r)})),
        ("api-gateway", 9, 1, lambda r, ds, t: (
            f"POST /v1/documents 202 {lat(ds, t, 140, 40, r)}ms", {"tenant": _tenant(r)})),
        ("api-gateway", 4, 1, lambda r, ds, t: (
            "Rate limit check passed", {"tenant": _tenant(r), "remaining": r.randint(50, 900)})),
        ("api-gateway", 0.6, 2, lambda r, ds, t: (
            "Client disconnected before response completed", {"tenant": _tenant(r)})),
        ("api-gateway", 0.3, 2, lambda r, ds, t: (
            "Deprecated header X-Legacy-Tenant used", {"tenant": _tenant(r)})),
        ("document-processing", 12, 1, lambda r, ds, t: (
            f"Document {_doc(r)} accepted pages={r.randint(1, 40)}", {"tenant": _tenant(r)})),
        ("document-processing", 12, 1, lambda r, ds, t: (
            f"Document {_doc(r)} processed in {lat(ds, t, 1800, 450, r)}ms pages={r.randint(1, 40)}",
            {"tenant": _tenant(r)})),
        ("document-processing", 5, 1, lambda r, ds, t: (
            f"Index update queued doc={_doc(r)}", {})),
        ("document-processing", 0.5, 2, lambda r, ds, t: (
            f"Slow stage 'layout' took {r.randint(2000, 3200)}ms doc={_doc(r)}", {})),
        ("document-processing", 0.25, 3, lambda r, ds, t: (
            f"Validation failed for document {_doc(r)}: unsupported mime type application/x-msdownload",
            {"tenant": _tenant(r)})),
        ("ocr-worker", 8, 1, lambda r, ds, t: (
            f"OCR page batch complete pages={r.randint(8, 64)} took {r.randint(600, 1400)}ms", {})),
        ("ocr-worker", 3, 1, lambda r, ds, t: (
            f"Model warm cache hit ratio={r.uniform(0.9, 0.97):.2f}", {})),
        ("ocr-worker", 3, 1, lambda r, ds, t: (
            "Worker heartbeat ok", {"pod": f"ocr-worker-{r.randint(0, 11)}"})),
        ("postgres", 7, 1, lambda r, ds, t: (
            f"connection checkout pool=main active={r.randint(20, 45)}/100", {})),
        ("postgres", 6, 1, lambda r, ds, t: (
            f"statement ok {lat(ds, t, 25, 10, r)}ms", {"query_id": f"q_{r.getrandbits(20):05x}"})),
        ("postgres", 0.3, 2, lambda r, ds, t: (
            f"slow statement {r.uniform(0.9, 1.6):.1f}s", {"query_id": f"q_{r.getrandbits(20):05x}"})),
        ("agent-orchestrator", 5, 1, lambda r, ds, t: (
            f"graph run {_doc(r)} node=planner->executor step={r.randint(1, 8)}", {})),
        ("agent-orchestrator", 3, 1, lambda r, ds, t: (
            f"graph run {_doc(r)} completed steps={r.randint(5, 9)} tokens={r.randint(4000, 9000)}", {})),
        ("vendor-client", 5, 1, lambda r, ds, t: (
            f"POST docuvision /v1/extract 200 {lat(ds, t, 800, 150, r)}ms", {})),
        ("vendor-client", 0.4, 2, lambda r, ds, t: (
            "docuvision POST /v1/extract -> 503 (attempt 1/3) retry succeeded", {})),
        ("auth-service", 4, 1, lambda r, ds, t: (
            f"token issued sub=svc-{r.choice(['gateway', 'docproc', 'batch', 'agent'])}", {})),
        ("auth-service", 0.4, 1, lambda r, ds, t: ("JWKS refreshed keys=3", {})),
        ("notification-service", 2, 1, lambda r, ds, t: (
            f"email queued template={r.choice(['receipt', 'digest', 'alert'])}", {"tenant": _tenant(r)})),
        ("notification-service", 0.3, 2, lambda r, ds, t: (
            "soft bounce for recipient <redacted>", {})),
    ]


class Dataset:
    def __init__(self, rng):
        self.rng = rng
        self.logs, self.traces = [], []
        self.deploys = []
        self.slow_queries, self.explain, self.schema = [], {}, {}
        self.jira, self.runbooks = {}, {}
        self.services = copy.deepcopy(SERVICES)
        self.behavior = {"logs_page_size": 1000, "metrics_empty_after": None, "fail_rules": []}
        self.alert = {}
        self.inputs = {}           # extra tool-facing files (rules file, scope message, ...)
        self.tz_mode = "utc"       # 'ist' => IST deploy history + naive trace timestamps (C3)
        self.lat_fn = lambda t: 1.0
        self.tags = {}
        self.metrics = {}
        self._build_baseline_metrics()

    # ---- metrics -----------------------------------------------------------
    def _build_baseline_metrics(self):
        r = self.rng
        for svc, name, base, amp, noise, dec in BASE_METRICS:
            vals = []
            for m in range(N_MIN):
                t = START + dt.timedelta(minutes=m)
                v = base * (1 + amp * rhythm(t)) * (1 + r.gauss(0, noise) if noise else 1)
                vals.append(round(max(v, 0), dec))
            self.metrics[(svc, name)] = vals
        # nightly job pulls slack down slowly from 02:00 even on a healthy night
        s = self.metrics[("nightly-batch", "projected_slack_min")]
        for m in range(minute_index(T(2, 0)), N_MIN):
            s[m] = round(s[m] - 0.05 * (m - minute_index(T(2, 0))), 1)

    def ramp_to(self, svc, name, start, end, target, stop=None, noise=0.04):
        """Ramp a series linearly to `target` between start..end, hold, optionally revert at `stop`."""
        s, r = self.metrics[(svc, name)], self.rng
        dec = next(d for sv, n, _, _, _, d in BASE_METRICS if sv == svc and n == name)
        ms, me = minute_index(start), max(minute_index(end), minute_index(start) + 1)
        mstop = minute_index(stop) if stop else N_MIN
        for m in range(max(ms, 0), min(mstop, N_MIN)):
            frac = min(1.0, (m - ms) / (me - ms))
            tgt = target * (1 + r.gauss(0, noise)) if noise else target
            s[m] = round(max(s[m] + (tgt - s[m]) * frac, 0), dec)

    # ---- logs / traces -------------------------------------------------------
    def plant(self, ts, svc, lvl, msg, props=None, tag=None, op=None):
        rec = {"ts": ts, "svc": svc, "lvl": lvl, "msg": msg, "props": props or {},
               "op": op or f"{self.rng.getrandbits(128):032x}", "tag": tag}
        self.logs.append(rec)
        return rec

    def ramp_lines(self, svc, lvl, start, end, r0, r1, fn, tag=None, stop=None):
        """Plant ~r0..r1 lines/min (linear over start..end, then hold) from fn(rng, t) -> (msg, props)."""
        r = self.rng
        m0, m1 = minute_index(start), max(minute_index(end), minute_index(start) + 1)
        mstop = minute_index(stop) if stop else N_MIN
        for m in range(max(m0, 0), min(mstop, N_MIN)):
            frac = min(1.0, (m - m0) / (m1 - m0))
            x = r0 + (r1 - r0) * frac
            n = int(x) + (1 if r.random() < x - int(x) else 0)
            for _ in range(n):
                t = START + dt.timedelta(minutes=m, milliseconds=r.randint(0, 59999))
                msg, props = fn(r, t)
                self.plant(t, svc, lvl, msg, props, tag)

    def plant_trace(self, ts, svc, name, target, dur_ms, success, code="", tag=None, op=None):
        rec = {"ts": ts, "svc": svc, "name": name, "target": target, "dur": dur_ms,
               "ok": success, "code": code, "tag": tag,
               "op": op or f"{self.rng.getrandbits(128):032x}"}
        self.traces.append(rec)
        return rec

    def add_deploy(self, ts, svc, did, author, repo, sha, message, diff, tag=None):
        self.deploys.append({"ts": ts, "service": svc, "id": did, "author": author, "repo": repo,
                             "sha": sha, "message": message, "diff": diff, "tag": tag})

    # ---- benign volume -----------------------------------------------------------
    def fill_benign(self, n_lines):
        r = self.rng
        tpls = _benign_templates()
        w_t = [t[1] for t in tpls]
        weights = [1.0 + 0.45 * rhythm(START + dt.timedelta(minutes=m)) for m in range(N_MIN)]
        tot = sum(weights)
        for m in range(N_MIN):
            x = n_lines * weights[m] / tot
            n = int(x) + (1 if r.random() < x - int(x) else 0)
            for _ in range(n):
                t = START + dt.timedelta(minutes=m, milliseconds=r.randint(0, 59999))
                tp = r.choices(tpls, weights=w_t)[0]
                svc, lvl, fn = tp[0], tp[2], tp[3]
                msg, props = fn(r, self, t)
                self.logs.append({"ts": t, "svc": svc, "lvl": lvl, "msg": msg, "props": props,
                                  "op": f"{r.getrandbits(128):032x}", "tag": None})
        # periodic housekeeping lines
        for m in range(0, N_MIN, 15):
            t = START + dt.timedelta(minutes=m, seconds=r.randint(0, 59))
            self.logs.append({"ts": t, "svc": "cert-refresher", "lvl": 1,
                              "msg": "certificate check ok (next renewal in 29d)", "props": {},
                              "op": f"{r.getrandbits(128):032x}", "tag": None})
        for m in range(0, N_MIN, 5):
            t = START + dt.timedelta(minutes=m, seconds=r.randint(0, 59))
            self.logs.append({"ts": t, "svc": "monitoring-agent", "lvl": 1,
                              "msg": f"exporter flush ok points={r.randint(180, 260)}", "props": {},
                              "op": f"{r.getrandbits(128):032x}", "tag": None})
        # nightly job progress after 02:00
        for m in range(minute_index(T(2, 0)), N_MIN):
            for _ in range(r.randint(1, 3)):
                t = START + dt.timedelta(minutes=m, milliseconds=r.randint(0, 59999))
                self.logs.append({"ts": t, "svc": "nightly-batch", "lvl": 1,
                                  "msg": f"shard {r.randint(1, 32)}/32 rows={r.randint(20000, 90000)} committed",
                                  "props": {}, "op": f"{r.getrandbits(128):032x}", "tag": None})
        # benign dependency traces (~12% of line count)
        names = [("POST docuvision /v1/extract", "docuvision", 800, 150, "vendor-client"),
                 ("SELECT documents", "postgres", 25, 10, "document-processing"),
                 ("GET ocr-worker /v1/pages", "ocr-worker", 900, 200, "document-processing"),
                 ("INSERT checkpoint_writes", "postgres", 3, 1, "agent-orchestrator"),
                 ("GET auth-service /jwks", "auth-service", 12, 4, "api-gateway")]
        for _ in range(max(500, n_lines // 8)):
            nm, tgt, mu, sd, svc = r.choice(names)
            m = r.randrange(N_MIN)
            t = START + dt.timedelta(minutes=m, milliseconds=r.randint(0, 59999))
            self.traces.append({"ts": t, "svc": svc, "name": nm, "target": tgt,
                                "dur": max(1, int(r.gauss(mu, sd) * (self.lat_fn(t) if tgt in ("ocr-worker",) else 1))),
                                "ok": True, "code": "200", "tag": None,
                                "op": f"{r.getrandbits(128):032x}"})

    # ---- finalize -------------------------------------------------------------------
    def finalize(self):
        """Sort, assign ItemIds, return {tag: [ids]} for the answer key."""
        self.logs.sort(key=lambda x: (x["ts"], x["svc"], x["msg"], x["op"]))
        self.traces.sort(key=lambda x: (x["ts"], x["svc"], x["name"], x["op"]))
        self.deploys.sort(key=lambda x: x["ts"])
        tags = {}
        for i, rec in enumerate(self.logs):
            rec["id"] = f"log-{i:07d}"
            if rec["tag"]:
                tags.setdefault(rec["tag"], []).append(rec["id"])
        for i, rec in enumerate(self.traces):
            rec["id"] = f"dep-{i:06d}"
            if rec["tag"]:
                tags.setdefault("trace_" + rec["tag"], []).append(rec["id"])
        for d in self.deploys:
            if d["tag"]:
                tags.setdefault("deploy_" + d["tag"], []).append(d["id"])
        self.tags = tags
        return tags
