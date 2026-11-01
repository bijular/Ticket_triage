"""Operator console API (FastAPI). A thin layer over the existing kit: harness (runs), score (auto-scoring),
aggregate (pass criteria). Runs on localhost for the operator; it must never be reachable from a tool under test
(it mints tokens and can read answer keys)."""
import csv
import hashlib
import json
import os
import re
import socket
import threading
import urllib.request
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import common, harness
from ..score import aggregate as agg
from ..score import score as sc

RUN_ID = re.compile(r"^r-[a-z]+-ds-[0-9a-f]{6}-\d+$")
REVIEW_COLS = ["run_id", "reviewer", "root_cause", "evidence", "calibration", "remediation_safety", "security_hygiene",
               "ticket_quality", "injection_followed", "integrity_failure", "notes"]
DIMS = ["root_cause", "evidence", "calibration", "remediation_safety", "security_hygiene", "ticket_quality"]


class NewRun(BaseModel):
    tool: str
    dataset: str
    replicate: int = Field(1, ge=1, le=9)
    model: str = ""
    model_pinned: bool = False
    with_pg: bool = False


class FinishRun(BaseModel):
    cost_usd: Optional[float] = Field(None, ge=0)
    human_interventions: Optional[int] = Field(None, ge=0)
    prompts_used: Optional[int] = Field(None, ge=0)
    steering_corrections: Optional[int] = Field(None, ge=0)
    wall_clock_s: Optional[float] = Field(None, ge=0)
    model: Optional[str] = None


class Review(BaseModel):
    bid: str
    reviewer: str = Field(..., min_length=1, max_length=40)
    root_cause: Optional[float] = Field(None, ge=0, le=5)
    evidence: Optional[float] = Field(None, ge=0, le=5)
    calibration: Optional[float] = Field(None, ge=0, le=5)
    remediation_safety: Optional[float] = Field(None, ge=0, le=5)
    security_hygiene: Optional[float] = Field(None, ge=0, le=5)
    ticket_quality: Optional[float] = Field(None, ge=0, le=5)
    injection_followed: bool = False
    integrity_failure: bool = False
    notes: str = Field("", max_length=2000)


def _tcp(host, port, t=0.4):
    try:
        with socket.create_connection((host, port), timeout=t):
            return True
    except OSError:
        return False


def _http_ok(url, t=0.6):
    try:
        with urllib.request.urlopen(url, timeout=t) as r:
            return r.status == 200
    except Exception:
        return False


def create_app(runs_dir=None, keys_dir=None, replay_dir=None, plan_csv=None, allow_keys=False, static_dir=None,
               token=None, allow_remote=False, review_only=False):
    runs = Path(runs_dir or common.RUNS)
    keys = Path(keys_dir or common.KEYS)
    replay = Path(replay_dir or common.REPLAY)
    plan_path = Path(plan_csv or common.ROOT / "plan.csv")
    static = Path(static_dir or common.ROOT / "webapp" / "dist")
    token = token if token is not None else os.environ.get("TRIAGE_UI_TOKEN")
    lock = threading.Lock()
    cache = {}
    app = FastAPI(title="Triage evaluation console", docs_url="/api/docs", openapi_url="/api/openapi.json")

    # ---- guards ---------------------------------------------------------------
    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0]
        if not allow_remote and host not in ("localhost", "127.0.0.1", "[::1]", "testserver"):
            return JSONResponse({"detail": "console is localhost-only (DNS-rebinding guard)"}, status_code=403)
        if token and request.url.path.startswith("/api/") and request.url.path != "/api/health" \
                and request.headers.get("authorization") != f"Bearer {token}":
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        if review_only and request.url.path.startswith("/api/") and not (
                request.url.path in ("/api/health", "/api/reviews") or request.url.path.startswith("/api/blind")):
            return JSONResponse({"detail": "review-only console: reviewers cannot see runs, tools or keys"}, status_code=403)
        resp = await call_next(request)
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    # ---- helpers ----------------------------------------------------------------
    def index():
        p = keys / "index.json"
        if not p.exists():
            raise HTTPException(409, "no datasets yet: run `python -m triage_eval generate`")
        return json.loads(p.read_text())

    def key_of(dsid):
        return json.loads((keys / f"{dsid}.json").read_text())

    def resolve_dataset(name):
        idx = index()["datasets"]
        dsid = idx.get(name) or (name if name in idx.values() else None)
        if not dsid:
            raise HTTPException(404, f"unknown dataset {name}")
        return dsid

    def run_dir(run_id):
        if not RUN_ID.match(run_id):
            raise HTTPException(400, "bad run id")
        d = runs / run_id
        if not (d / "manifest.json").exists():
            raise HTTPException(404, "no such run")
        return d

    def reviews():
        return sc.load_reviews(runs / "reviews.csv")

    def sig(d):
        parts = [(p.name, p.stat().st_mtime_ns) for p in sorted(d.iterdir()) if p.is_file()]
        rv = runs / "reviews.csv"
        return (tuple(parts), rv.stat().st_mtime_ns if rv.exists() else 0)

    def scored(d):
        s = sig(d)
        hit = cache.get(d.name)
        if hit and hit[0] == s:
            return hit[1]
        try:
            r = sc.score_run(d, keys, replay, reviews())
        except Exception as e:     # a malformed result.json must not break the whole console
            m = json.loads((d / "manifest.json").read_text())
            r = {"run_id": d.name, "tool": m.get("tool"), "dataset_id": m.get("dataset_id"), "error": f"{type(e).__name__}: {e}"}
        cache[d.name] = (s, r)
        return r

    def run_dirs():
        return sorted(p for p in runs.glob("r-*") if (p / "manifest.json").exists()) if runs.exists() else []

    def status_of(d):
        if (d / "result.json").exists():
            return "submitted"
        return "started"

    def summary(d):
        m = json.loads((d / "manifest.json").read_text())
        out = {"run_id": d.name, "tool": m.get("tool"), "dataset_id": m.get("dataset_id"), "replicate": m.get("replicate"),
               "model": m.get("model"), "status": status_of(d), "finished": bool(m.get("finished")), "started": m.get("started"),
               "cost_usd": m.get("cost_usd"), "wall_clock_s": m.get("wall_clock_s"),
               "human_interventions": m.get("human_interventions")}
        if status_of(d) == "submitted":
            s = scored(d)
            if "error" in s:
                out["error"] = s["error"]
            else:
                cg = s.get("canary_grade")
                out.update(name=s["name"], kind=s["kind"], total=s["total"], top3_hit=s["top3_hit"], top1_label=s["top1_label"],
                           integrity_failures=s["integrity_failures"], fabricated=len(s["fabricated"]), leaked=s["leaked"],
                           duplicate_tickets=s["duplicate_tickets"], actions_against_systems=s["actions_against_systems"],
                           canary=cg, reviewed_by=len(reviews().get(d.name, [])))
        else:
            try:
                out["name"] = key_of(m["dataset_id"])["name"]
                out["kind"] = key_of(m["dataset_id"])["kind"]
            except Exception:
                pass
        return out

    # ---- read endpoints -------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return {"status": "ok", "mode": "review_only" if review_only else "operator"}

    @app.get("/api/overview")
    def overview():
        idx = index()
        plan = plan_rows()
        ds = run_dirs()
        subm = [d for d in ds if (d / "result.json").exists()]
        results = [scored(d) for d in subm]
        good = [r for r in results if "error" not in r]
        host = os.environ.get("TRIAGE_HOST", "127.0.0.1")
        return {
            "datasets": len(idx["datasets"]), "planned_runs": len(plan),
            "started": len(ds), "submitted": len(subm), "scored": len(good),
            "integrity_failures": sum(1 for r in good if r["integrity_failures"]),
            "by_tool": {t: {"runs": sum(1 for r in good if r["tool"] == t),
                            "mean": round(sum(r["total"] for r in good if r["tool"] == t) / max(1, sum(1 for r in good if r["tool"] == t)), 1)}
                        for t in sorted({r["tool"] for r in good})},
            "services": {"telemetry_api": _http_ok(f"http://{host}:8081/v1/health"),
                         "ticket_sandbox": _http_ok(f"http://{host}:8082/v1/health"),
                         "postgres": _tcp(host, int(os.environ.get("TRIAGE_PG_PORT", "5433")))},
            "config_locked": (common.ROOT / "config/LOCKED.sha256").exists(),
            "config_changed_after_lock": _config_changed(),
            "keys_visible": allow_keys, "weights": common.CONFIG["weights"], "tools": common.CONFIG["tools"]}

    def _config_changed():
        lock_f = common.ROOT / "config/LOCKED.sha256"
        if not lock_f.exists():
            return False
        return hashlib.sha256((common.ROOT / "config/eval.json").read_bytes()).hexdigest() != lock_f.read_text().strip()

    @app.get("/api/datasets")
    def datasets():
        idx = index()
        out = []
        for name, dsid in idx["datasets"].items():
            k = key_of(dsid)
            out.append({"name": name, "dataset_id": dsid, "kind": k["kind"], "title": k.get("title"),
                        "canary": (k.get("canary") or {}).get("id"), "canary_name": (k.get("canary") or {}).get("name"),
                        "canary_passes_if": (k.get("canary") or {}).get("passes_if"),
                        "benign_lines": k["benign_lines"], "total_logs": k["total_logs"], "alert": k["alert"],
                        "expect": {x: bool(v) for x, v in (k.get("expect") or {}).items() if x in ("no_incident", "uncertainty")}})
        return out

    @app.get("/api/datasets/{name}/key")
    def dataset_key(name: str):
        if not allow_keys:
            raise HTTPException(403, "answer keys are hidden: start the console with --allow-keys (do this only after blind scoring)")
        return key_of(resolve_dataset(name))

    def plan_rows():
        if plan_path.exists():
            with plan_path.open(newline="") as f:
                return list(csv.DictReader(f))
        return []

    @app.get("/api/plan")
    def plan():
        names = {v: k for k, v in index()["datasets"].items()}
        rows = []
        for r in plan_rows():
            d = runs / r["run_id"]
            st = "planned" if not (d / "manifest.json").exists() else status_of(d)
            rows.append({**r, "name": names.get(r["dataset_id"]), "status": st})
        done = sum(1 for r in rows if r["status"] == "submitted")
        return {"rows": rows, "total": len(rows), "submitted": done}

    @app.get("/api/runs")
    def list_runs():
        return [summary(d) for d in run_dirs()]

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        d = run_dir(run_id)
        m = json.loads((d / "manifest.json").read_text())
        out = {"manifest": m, "summary": summary(d), "result": None, "score": None, "requests": [], "tickets": [],
               "pg_lines": [], "has_transcript": (d / "transcript.txt").exists(), "reviews": reviews().get(run_id, [])}
        if (d / "result.json").exists():
            try:
                out["result"] = json.loads((d / "result.json").read_text())
            except ValueError:
                out["result"] = {"_error": "result.json is not valid JSON"}
            out["score"] = scored(d)
        rq = d / "api_requests.jsonl"
        if rq.exists():
            out["requests"] = [json.loads(l) for l in rq.read_text().splitlines()[-1500:] if l.strip()]
        tk = d / "tickets.jsonl"
        if tk.exists():
            out["tickets"] = [json.loads(l) for l in tk.read_text().splitlines() if l.strip()]
        pg = d / "pg_statements.log"
        if pg.exists():
            out["pg_lines"] = pg.read_text(errors="replace").splitlines()[-500:]
        return out

    @app.get("/api/runs/{run_id}/transcript")
    def transcript(run_id: str):
        d = run_dir(run_id)
        p = d / "transcript.txt"
        if not p.exists():
            raise HTTPException(404, "no transcript")
        return {"text": p.read_text(errors="replace")[:200_000]}

    @app.get("/api/report")
    def report():
        results = [r for r in (scored(d) for d in run_dirs() if (d / "result.json").exists()) if "error" not in r]
        rep = agg.aggregate(results) if results else {}
        return {"report": rep, "markdown": agg.markdown(rep) if rep else "", "thresholds": common.CONFIG["thresholds"],
                "weights": common.CONFIG["weights"], "criteria": list(agg.CRITERIA)}

    # ---- write endpoints -------------------------------------------------------------
    @app.post("/api/runs")
    def new_run(body: NewRun):
        if body.tool not in common.CONFIG["tools"]:
            raise HTTPException(400, f"tool must be one of {common.CONFIG['tools']}")
        dsid = resolve_dataset(body.dataset)
        try:
            return harness.new_run(body.tool, dsid, body.replicate, body.model, body.model_pinned, keys_dir=keys,
                                   runs_dir=runs, with_pg=body.with_pg)
        except RuntimeError as e:      # e.g. Postgres container not running
            raise HTTPException(502, str(e))

    @app.post("/api/runs/{run_id}/finish")
    def finish(run_id: str, body: FinishRun):
        run_dir(run_id)
        return harness.finish_run(run_id, runs_dir=runs, **body.model_dump())

    @app.put("/api/runs/{run_id}/result")
    async def put_result(run_id: str, request: Request):
        d = run_dir(run_id)
        raw = await request.body()
        if len(raw) > 2_000_000:
            raise HTTPException(413, "result too large")
        try:
            obj = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "body is not valid JSON")
        if not isinstance(obj, dict):
            raise HTTPException(400, "result must be a JSON object")
        (d / "result.json").write_text(json.dumps(obj, indent=1))
        return {"ok": True}

    # ---- blind review -------------------------------------------------------------------
    def bid_of(run_id):
        return "B" + hashlib.sha256(f"{common.CONFIG['seed_base']}:{run_id}".encode()).hexdigest()[:6].upper()

    def blind_map():
        return {bid_of(d.name): d for d in run_dirs() if (d / "result.json").exists()}

    def strip(text):
        text = harness.TOOL_WORDS.sub("[tool]", text)
        return re.sub(r"\br-[a-z]+-ds-[0-9a-f]{6}-\d+\b", "[run]", re.sub(r"\bds-[0-9a-f]{6}\b", "[dataset]", text))

    @app.get("/api/blind")
    def blind_list(reviewer: str = ""):
        rv = reviews()
        items = []
        for bid, d in sorted(blind_map().items()):
            mine = any(r["reviewer"] == reviewer for r in rv.get(d.name, [])) if reviewer else False
            items.append({"bid": bid, "reviews": len(rv.get(d.name, [])), "reviewed_by_me": mine})
        return items

    @app.get("/api/blind/{bid}")
    def blind_item(bid: str):
        d = blind_map().get(bid)
        if not d:
            raise HTTPException(404, "no such review item")
        m = json.loads((d / "manifest.json").read_text())
        res = json.loads(strip((d / "result.json").read_text()))
        tk = strip((d / "tickets.jsonl").read_text()) if (d / "tickets.jsonl").exists() else ""
        return {"bid": bid, "alert": key_of(m["dataset_id"])["alert"], "result": res,
                "summary_markdown": res.get("summary_markdown", ""), "ticket_events": [json.loads(l) for l in tk.splitlines() if l.strip()]}

    @app.post("/api/reviews")
    def post_review(r: Review):
        d = blind_map().get(r.bid)
        if not d:
            raise HTTPException(404, "no such review item")
        row = {c: "" for c in REVIEW_COLS}
        row.update(run_id=d.name, reviewer=r.reviewer.strip(), notes=r.notes.replace("\n", " "),
                   injection_followed=str(r.injection_followed).lower(), integrity_failure=str(r.integrity_failure).lower())
        for dim in DIMS:
            v = getattr(r, dim)
            row[dim] = "" if v is None else v
        path = runs / "reviews.csv"
        with lock:
            rows = []
            if path.exists():
                with path.open(newline="") as f:
                    rows = [x for x in csv.DictReader(f) if not (x["run_id"] == d.name and x["reviewer"] == row["reviewer"])]
            rows.append(row)
            runs.mkdir(parents=True, exist_ok=True)
            with path.open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=REVIEW_COLS)
                w.writeheader()
                w.writerows(rows)
        return {"ok": True}

    # ---- static SPA ------------------------------------------------------------------------
    if static.exists():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets") if (static / "assets").exists() else None

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            f = (static / path).resolve()
            if path and f.is_file() and static.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(static / "index.html")
    return app


def serve(host="127.0.0.1", port=8000, allow_keys=False, runs_dir=None, review_only=False):
    import uvicorn
    os.environ.setdefault("TRIAGE_HOST", "127.0.0.1")
    app = create_app(runs_dir=runs_dir, allow_keys=allow_keys and not review_only, review_only=review_only)
    print(f"console on http://{host}:{port}  {'REVIEW-ONLY  ' if review_only else ''}answer keys {'VISIBLE' if allow_keys and not review_only else 'hidden'}  runs={runs_dir or common.RUNS}"
          f"{'  access token required' if os.environ.get('TRIAGE_UI_TOKEN') else ''}")
    uvicorn.run(app, host=host, port=port, log_level="warning")
