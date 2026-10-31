"""CLI:  python -m triage_eval <command> ...   (run with -h for the list)"""
import argparse
import json
import sys
from pathlib import Path

from . import common, harness
from .gen import build as gb


def main(argv=None):
    ap = argparse.ArgumentParser(prog="triage_eval", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="build all replay datasets, answer keys and prompts (deterministic)")
    g.add_argument("--lines", type=int, help="benign log lines per incident (default from config: 50000)")
    g.add_argument("--no-optional", action="store_true", help="skip optional C7b/C7c variants")

    s = sub.add_parser("serve-data", help="read-only telemetry API (GET only)")
    s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8081)
    s.add_argument("--flaky-rate", type=float, help="fraction of first-time requests that get 429/503 (default config)")
    t = sub.add_parser("serve-tickets", help="sandbox ticket API")
    t.add_argument("--host", default="127.0.0.1"); t.add_argument("--port", type=int, default=8082)

    n = sub.add_parser("new-run", help="create a run folder and print scoped tokens (expire in 5 min, renewable)")
    n.add_argument("--tool", required=True, choices=common.CONFIG["tools"])
    n.add_argument("--dataset", required=True, help="name (inc01..inc08, c1..c9, h1, h2) or ds-id")
    n.add_argument("--replicate", type=int, default=1)
    n.add_argument("--with-pg", action="store_true", help="also create a per-run read-only Postgres role (needs `docker compose up postgres` + pg-seed)")
    n.add_argument("--model", default=""); n.add_argument("--model-pinned", action="store_true")
    pg = sub.add_parser("pg-seed", help="(re)create and seed the disposable Postgres databases, one per dataset")
    pg.add_argument("names", nargs="*", help="dataset names (default: all)")
    f = sub.add_parser("finish-run", help="record cost / interventions / model on a run")
    f.add_argument("run_id")
    for k in ("cost-usd", "human-interventions", "prompts-used", "steering-corrections"):
        f.add_argument("--" + k, type=float)
    f.add_argument("--model"); f.add_argument("--wall-clock-s", type=float)

    sub.add_parser("plan", help="write plan.csv (171 runs; add --optional for the extra C7 variants)").add_argument("--optional", action="store_true")
    sub.add_parser("score", help="auto-score every run in runs/ (applies runs/reviews.csv overrides)")
    sub.add_parser("report", help="aggregate per tool and write report.md / report.json")
    sub.add_parser("blind", help="export tool-anonymised review items + reviews template")
    d = sub.add_parser("drift", help="C5 instruction-drift check on a run transcript")
    d.add_argument("run_id"); d.add_argument("--dataset", default="c5")
    sub.add_parser("c12", help="build the reviewer rubber-stamping batch (human test)")
    u = sub.add_parser("serve-ui", help="operator console (FastAPI + React). Needs: pip install -r requirements-web.txt")
    u.add_argument("--host", default="127.0.0.1"); u.add_argument("--port", type=int, default=8000)
    u.add_argument("--review-only", action="store_true", help="expose ONLY blind review (for reviewers): no runs, tools, datasets or keys")
    u.add_argument("--runs-dir", help="read/write runs here instead of ./runs")
    u.add_argument("--allow-keys", action="store_true", help="let the console show answer keys (only after blind scoring)")
    sub.add_parser("init-env", help="write .env (EVAL_SECRET, container uid/gid) shared by docker compose and the host CLI")
    sub.add_parser("lock", help="hash config/eval.json so threshold edits after the first run are detectable")
    sub.add_parser("verify", help="verify generated data matches the answer keys' manifest hashes + lock file")
    a = ap.parse_args(argv)

    if a.cmd == "generate":
        idx = gb.build_all(a.lines, include_optional=not a.no_optional)
        print(f"generated {len(idx)} datasets -> {common.REPLAY} ; keys -> {common.KEYS} (keep OUT of tool reach)")
    elif a.cmd == "serve-data":
        from .api import data_server
        data_server.serve(a.host, a.port, flaky_rate=a.flaky_rate)
    elif a.cmd == "serve-tickets":
        from .api import ticket_server
        ticket_server.serve(a.host, a.port)
    elif a.cmd == "new-run":
        out = harness.new_run(a.tool, a.dataset, a.replicate, a.model, a.model_pinned, with_pg=a.with_pg)
        print(json.dumps(out, indent=1))
        print("\n# shell exports:\n" + "\n".join(f"export {k}={v}" for k, v in out["env"].items()), file=sys.stderr)
    elif a.cmd == "pg-seed":
        from .pg import seed as pgseed
        pgseed.seed(a.names or None)
    elif a.cmd == "finish-run":
        m = harness.finish_run(a.run_id, cost_usd=a.cost_usd, human_interventions=a.human_interventions,
                               prompts_used=a.prompts_used, steering_corrections=a.steering_corrections,
                               model=a.model, wall_clock_s=a.wall_clock_s)
        print(json.dumps(m, indent=1))
    elif a.cmd == "plan":
        rows = harness.plan(include_optional=a.optional)
        print(f"wrote plan.csv with {len(rows)} runs")
    elif a.cmd == "score":
        from .score import score
        rs = score.score_all()
        for r in rs:
            print(f"{r['run_id']:<28} {r['name']:<8} total={r['total']:>5}  top3={r['top3_hit']}  "
                  f"{'INTEGRITY: ' + '; '.join(r['integrity_failures']) if r['integrity_failures'] else ''}")
    elif a.cmd == "report":
        from .score import aggregate, score
        rs = score.score_all()
        rep = aggregate.aggregate(rs)
        (common.ROOT / "report.json").write_text(json.dumps(rep, indent=1, sort_keys=True))
        (common.ROOT / "report.md").write_text(aggregate.markdown(rep))
        print(aggregate.markdown(rep))
    elif a.cmd == "blind":
        print(f"exported {harness.blind_export()} items to blind/")
    elif a.cmd == "drift":
        print(json.dumps(harness.drift_check(a.run_id, a.dataset), indent=1))
    elif a.cmd == "c12":
        print(json.dumps(harness.c12_batch(), indent=1))
    elif a.cmd == "serve-ui":
        try:
            from .web import app as webapp
        except ImportError as e:
            raise SystemExit(f"{e}\ninstall the console deps first: python3 -m venv .venv && .venv/bin/pip install -r requirements-web.txt")
        webapp.serve(a.host, a.port, a.allow_keys, a.runs_dir, a.review_only)
    elif a.cmd == "init-env":
        import os, secrets
        envf = common.ROOT / ".env"
        if envf.exists():
            print(".env already exists; leaving it (delete it to rotate the secret)")
        else:
            envf.write_text(f"EVAL_SECRET={secrets.token_hex(32)}\nTRIAGE_UID={os.getuid()}\nTRIAGE_GID={os.getgid()}\n")
            envf.chmod(0o600)
            print("wrote .env")
    elif a.cmd == "lock":
        import hashlib
        h = hashlib.sha256((common.ROOT / "config/eval.json").read_bytes()).hexdigest()
        (common.ROOT / "config/LOCKED.sha256").write_text(h + "\n")
        print("locked config/eval.json", h[:12])
    elif a.cmd == "verify":
        import hashlib
        lock = common.ROOT / "config/LOCKED.sha256"
        if lock.exists():
            cur = hashlib.sha256((common.ROOT / "config/eval.json").read_bytes()).hexdigest()
            print("config lock:", "OK" if cur == lock.read_text().strip() else "CHANGED after lock!")
        bad = 0
        for p in common.KEYS.glob("ds-*.json"):
            k = json.loads(p.read_text())
            mf = common.REPLAY / k["dataset_id"] / "MANIFEST.json"
            ok = mf.exists() and hashlib.sha256(mf.read_bytes()).hexdigest() == k["manifest_sha256"]
            bad += not ok
            if not ok:
                print("MISMATCH", k["name"], k["dataset_id"])
        print("datasets verified" if not bad else f"{bad} dataset(s) differ from answer keys")
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
