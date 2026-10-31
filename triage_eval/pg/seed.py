"""Real, disposable PostgreSQL for the DB-backed incidents (Tier A for Postgres).

One database per dataset (ds_xxxxxx), seeded deterministically from SQL (no RNG, so identical on every
run for a pinned Postgres version). Scaled down from the replay story: plans/EXPLAIN are *real* but
timings are smaller than the replayed 11 s. Tools get a per-run read-only role with an expiry.
"""
import json
import os
import re
import secrets
import shlex
import subprocess
import time
from pathlib import Path

from .. import common
from ..gen import build as gb

PSQL = os.environ.get("TRIAGE_PSQL", "docker compose exec -T postgres psql -U postgres")
HOST_PORT = os.environ.get("TRIAGE_PG_PORT", "5433")
PROFILE = {"inc02": "missing_index", "inc04": "checkpoint_loop"}     # everything else: healthy


def dbname(dsid):
    return dsid.replace("-", "_")


def rolename(run_id):
    return "ro_" + re.sub(r"[^a-z0-9_]", "_", run_id.lower())[:50]


def psql(sql, db="postgres", capture=False):
    cmd = shlex.split(PSQL) + ["-d", db, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-A", "-t", "-f", "-"]
    r = subprocess.run(cmd, input=sql, text=True, capture_output=True, cwd=common.ROOT)
    if r.returncode:
        raise RuntimeError(f"psql failed: {r.stderr.strip()[:500]}")
    return r.stdout if capture else None


BASE_SQL = """
CREATE TABLE tenants(id text PRIMARY KEY, name text NOT NULL);
CREATE TABLE documents(id bigserial PRIMARY KEY, tenant_id text NOT NULL, status text NOT NULL, pages int NOT NULL,
                       created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL);
CREATE INDEX documents_tenant_idx ON documents(tenant_id);
CREATE INDEX documents_created_idx ON documents(created_at);
CREATE TABLE agent_runs(run_id text PRIMARY KEY, started_at timestamptz NOT NULL, prompt_version text NOT NULL,
                        steps int NOT NULL, tokens int NOT NULL, status text NOT NULL);
CREATE TABLE checkpoint_writes(thread_id text NOT NULL, step int NOT NULL, channel text NOT NULL,
                               blob_bytes int NOT NULL, written_at timestamptz NOT NULL);
CREATE SCHEMA ops;
CREATE TABLE ops.slow_query_snapshots(snapshot timestamptz, query_id text, query text, calls bigint, mean_ms numeric, max_ms numeric, rows bigint);
INSERT INTO tenants SELECT 'tnt-' || lpad(g::text, 4, '0'), 'Tenant ' || g FROM generate_series(1, 500) g;
INSERT INTO documents(tenant_id, status, pages, created_at, updated_at)
SELECT 'tnt-' || lpad((1 + (g * 7919 % 500))::text, 4, '0'),
       CASE WHEN g % 20 = 0 THEN 'pending' WHEN g % 50 = 1 THEN 'failed' ELSE 'done' END,
       1 + (g * 31 % 40), ts, ts + ((g % 600) * interval '1 second')
FROM (SELECT g, timestamptz '2026-03-01 00:00:00+00' + ((g * 13 % 1000000) * interval '1 second') AS ts
      FROM generate_series(1, 200000) g) s;
INSERT INTO agent_runs
SELECT 'run-v13-' || g, timestamptz '2026-03-11 12:00:00+00' + ((g * 3 % 54000) * interval '1 second'), 'v13',
       5 + (g % 5), 4000 + (g * 7 % 5000), 'ok'
FROM generate_series(1, 30000) g;
"""

MISSING_INDEX_SQL = """
-- tenant tnt-0042 grew huge; there is no index covering (tenant_id, status, updated_at)
INSERT INTO documents(tenant_id, status, pages, created_at, updated_at)
SELECT 'tnt-0042', CASE WHEN g % 100 < 45 THEN 'pending' ELSE 'done' END, 1 + (g % 40),
       ts, ts + ((g % 600) * interval '1 second')
FROM (SELECT g, timestamptz '2026-03-01 00:00:00+00' + ((g * 17 % 1000000) * interval '1 second') AS ts
      FROM generate_series(1, 400000) g) s;
"""

CHECKPOINT_SQL = """
INSERT INTO agent_runs
SELECT 'run-v14-' || g, timestamptz '2026-03-12 01:45:00+00' + ((g * 13 % 4500) * interval '1 second'), 'v14',
       25, 60000 + (g * 7 % 30000), 'GraphRecursionError'
FROM generate_series(1, 6000) g;
INSERT INTO checkpoint_writes
SELECT 'run-v14-' || r, s, CASE WHEN s % 2 = 0 THEN 'planner' ELSE 'search_kb' END, 2000 + (s * 37 % 4000),
       timestamptz '2026-03-12 01:45:00+00' + ((r * 13 % 4500) * interval '1 second')
FROM generate_series(1, 6000) r, generate_series(1, 25) s;
INSERT INTO checkpoint_writes
SELECT 'run-v13-' || r, s, CASE WHEN s % 2 = 0 THEN 'planner' ELSE 'executor' END, 1500 + (s * 37 % 2000),
       timestamptz '2026-03-11 12:00:00+00' + ((r * 3 % 54000) * interval '1 second')
FROM generate_series(1, 3000) r, generate_series(1, 6) s;
"""


def _lit(s):
    return "'" + str(s).replace("'", "''") + "'"


def seed_sql(name, replay_dir=None):
    spec = gb.catalog()[name]
    dsid = common.dataset_id(name)
    profile = PROFILE.get(spec["incident"], "healthy")
    sql = BASE_SQL + (MISSING_INDEX_SQL if profile == "missing_index" else "") + (CHECKPOINT_SQL if profile == "checkpoint_loop" else "")
    snaps = json.loads((Path(replay_dir or common.REPLAY) / dsid / "db" / "slow_queries.json").read_text())
    for sn in snaps:
        for q in sn["queries"]:
            sql += ("INSERT INTO ops.slow_query_snapshots VALUES (%s,%s,%s,%s,%s,%s,%s);\n" % (
                _lit(sn["snapshot"]), _lit(q["query_id"]), _lit(q["query"]), q["calls"], q["mean_ms"], q["max_ms"], q["rows"]))
    sql += ("ANALYZE;\nGRANT USAGE ON SCHEMA public, ops TO triage_ro_group;\n"
            "GRANT SELECT ON ALL TABLES IN SCHEMA public, ops TO triage_ro_group;\n"
            "")
    return dsid, profile, sql


def init_cluster():
    psql("""DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='triage_ro_group') THEN CREATE ROLE triage_ro_group NOLOGIN; END IF;
            END $$;
            """)    # no pg_stat_statements / pg_read_all_stats: that would expose other runs' queries


def seed(names=None, replay_dir=None, verbose=True):
    init_cluster()
    for name in (names or gb.BASE_NAMES + gb.CANARY_NAMES + gb.HOLDOUT_NAMES + gb.OPTIONAL_NAMES):
        dsid, profile, sql = seed_sql(name, replay_dir)
        db = dbname(dsid)
        psql(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE);')
        psql(f'CREATE DATABASE "{db}";')
        psql(f'REVOKE CONNECT, TEMPORARY ON DATABASE "{db}" FROM PUBLIC;')
        psql(sql, db)
        if verbose:
            print(f"seeded {name:<7} {db} profile={profile}")


def create_run_role(run_id, dsid, ttl_s=None):
    """Per-run read-only login that can connect to ONLY this dataset's database and expires."""
    ttl_s = ttl_s or common.CONFIG["max_session_seconds"]
    role, pw, db = rolename(run_id), secrets.token_urlsafe(18), dbname(dsid)
    until = time.strftime("%Y-%m-%d %H:%M:%S+00", time.gmtime(time.time() + ttl_s))
    psql(f"""DROP ROLE IF EXISTS "{role}";
             CREATE ROLE "{role}" LOGIN PASSWORD '{pw}' VALID UNTIL '{until}' CONNECTION LIMIT 5 IN ROLE triage_ro_group;
             ALTER ROLE "{role}" SET default_transaction_read_only = on;
             ALTER ROLE "{role}" SET statement_timeout = '10s';
             GRANT CONNECT ON DATABASE "{db}" TO "{role}";""")
    return f"postgresql://{role}:{pw}@127.0.0.1:{HOST_PORT}/{db}"


def collect_log(run_id, out_path):
    """Capture this run's statements from the server log (log_statement=all) for the scorer."""
    role = rolename(run_id)
    cmd = shlex.split(os.environ.get("TRIAGE_PG_LOGS", "docker compose logs --no-color --no-log-prefix postgres"))
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=common.ROOT)
    lines = [l for l in r.stdout.splitlines() if f" {role}@" in l or f"user={role} " in l]
    Path(out_path).write_text("\n".join(lines) + ("\n" if lines else ""))
    return len(lines)
