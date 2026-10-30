"""Builds replay datasets, answer keys and tool-facing prompts. Deterministic for a given seed."""
import datetime as dt
import gzip
import hashlib
import json
import random
import shutil
from pathlib import Path

from .. import common
from .core import START, Dataset, LEVELS, iso, T
from .incidents import INCIDENTS
from .overlays import CATALOG_OVERLAYS

BASE_NAMES = [f"inc{n:02d}" for n in range(1, 9)]
HOLDOUT_NAMES = ["h1", "h2"]
CANARY_NAMES = [n for n, v in CATALOG_OVERLAYS.items() if not v[3]]
OPTIONAL_NAMES = [n for n, v in CATALOG_OVERLAYS.items() if v[3]]


def catalog():
    """name -> dict(kind, incident, overlay, mult, seed_name)."""
    cat = {}
    for n in BASE_NAMES:
        cat[n] = dict(kind="incident", incident=n, overlay=None, mult=1.0, seed_name=n)
    for n in HOLDOUT_NAMES:
        cat[n] = dict(kind="holdout", incident=n, overlay=None, mult=1.0, seed_name=n)
    for n, (inc, fn, mult, opt) in CATALOG_OVERLAYS.items():
        cat[n] = dict(kind="canary_optional" if opt else "canary", incident=inc, overlay=fn, mult=mult,
                      seed_name=inc)
    return cat


def _gz(path: Path, data: bytes):
    path.write_bytes(gzip.compress(data, compresslevel=6, mtime=0))


def _dumps(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _ist(t):
    return (t + dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M:%S") + " IST"


def build(name, lines=None, out_replay=None, out_keys=None, out_prompts=None):
    cfg = common.CONFIG
    spec = catalog()[name]
    lines = lines if lines is not None else cfg["benign_lines_per_incident"]
    out_replay = Path(out_replay or common.REPLAY)
    out_keys = Path(out_keys or common.KEYS)
    out_prompts = Path(out_prompts or common.PROMPTS)
    dsid = common.dataset_id(name)

    rng = random.Random(common.seed_for(spec["seed_name"]))
    ds = Dataset(rng)
    key = INCIDENTS[spec["incident"]](ds)
    if spec["overlay"]:
        main_rng = ds.rng
        ds.rng = random.Random(common.seed_for(name + ":overlay"))   # keeps benign volume identical to the base
        frag = spec["overlay"](ds)
        ds.rng = main_rng
        for k, v in frag.items():
            if k == "expect_extra":
                key.setdefault("expect", {}).update(
                    {kk: (key["expect"].get(kk, []) + vv) if isinstance(vv, list) else vv for kk, vv in v.items()})
            elif k == "decoy_patterns_extra":
                key["decoy_patterns"] = key.get("decoy_patterns", []) + v
            else:
                key[k] = v
    ds.fill_benign(int(lines * spec["mult"]))
    tags = ds.finalize()

    d = out_replay / dsid
    if d.exists():
        shutil.rmtree(d)
    (d / "db").mkdir(parents=True)
    ist = ds.tz_mode == "ist"

    logs = [{"ItemId": r["id"], "TimeGenerated": iso(r["ts"]), "AppRoleName": r["svc"],
             "SeverityLevel": LEVELS[r["lvl"]], "Message": r["msg"], "OperationId": r["op"],
             "Properties": r["props"]} for r in ds.logs]
    _gz(d / "logs.jsonl.gz", "\n".join(_dumps(x) for x in logs).encode() + b"\n")

    def tts(t):
        return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}" if ist else iso(t)
    traces = [{"ItemId": r["id"], "TimeGenerated": tts(r["ts"]), "AppRoleName": r["svc"], "Name": r["name"],
               "Target": r["target"], "DurationMs": r["dur"], "Success": r["ok"], "ResultCode": r["code"],
               "OperationId": r["op"]} for r in ds.traces]
    _gz(d / "traces.jsonl.gz", "\n".join(_dumps(x) for x in traces).encode() + b"\n")

    series = {f"{svc}/{m}": v for (svc, m), v in sorted(ds.metrics.items())}
    _gz(d / "metrics.json.gz", _dumps({"interval": "PT1M", "start": iso(START), "series": series}).encode())

    deploys = []
    for x in ds.deploys:
        e = {"id": x["id"], "service": x["service"], "author": x["author"], "repo": x["repo"], "sha": x["sha"],
             "message": x["message"], "diff": x["diff"]}
        if ist:
            e["deployed_at"], e["tz"] = _ist(x["ts"]), "IST"
        else:
            e["deployed_at"] = iso(x["ts"])
        deploys.append(e)
    (d / "deploys.json").write_text(_dumps(deploys))
    (d / "alert.json").write_text(_dumps(ds.alert))
    (d / "services.json").write_text(_dumps([{"name": k, "description": v} for k, v in sorted(ds.services.items())]))
    (d / "db" / "slow_queries.json").write_text(_dumps(ds.slow_queries))
    (d / "db" / "explain.json").write_text(_dumps(ds.explain))
    (d / "db" / "schema.json").write_text(_dumps(ds.schema))
    if ds.jira:
        (d / "jira").mkdir()
        for k, v in ds.jira.items():
            (d / "jira" / f"{k}.json").write_text(_dumps(v))
    if ds.runbooks:
        (d / "runbooks").mkdir()
        for k, v in ds.runbooks.items():
            (d / "runbooks" / f"{k}.md").write_text(v)
    (d / "behavior.json").write_text(_dumps(ds.behavior))
    manifest = {p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(d.rglob("*")) if p.is_file()}
    (d / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True))

    # answer key -> separate directory, never inside the replay tree
    out_keys.mkdir(parents=True, exist_ok=True)
    key.update({"dataset_id": dsid, "name": name, "kind": spec["kind"], "incident": spec["incident"],
                "seed": common.seed_for(spec["seed_name"]), "benign_lines": int(lines * spec["mult"]),
                "total_logs": len(ds.logs), "tags": tags, "alert": ds.alert["text"],
                "metrics_empty_after": ds.behavior["metrics_empty_after"], "tz_mode": ds.tz_mode,
                "manifest_sha256": hashlib.sha256((d / "MANIFEST.json").read_bytes()).hexdigest()})
    (out_keys / f"{dsid}.json").write_text(json.dumps(key, indent=1, sort_keys=True))

    out_prompts.mkdir(parents=True, exist_ok=True)
    (out_prompts / f"{dsid}.md").write_text(render_prompt(dsid, ds))
    for fname, text in ds.inputs.items():
        if fname.startswith("operator_"):           # instructions for the human operator, never shown to a tool
            (out_prompts.parent / "operator_notes").mkdir(parents=True, exist_ok=True)
            (out_prompts.parent / "operator_notes" / f"{dsid}.txt").write_text(text)
        elif fname == "AGENTS.md":                  # rules file the tool should pick up from its workspace
            (out_prompts.parent / "inputs" / dsid).mkdir(parents=True, exist_ok=True)
            (out_prompts.parent / "inputs" / dsid / fname).write_text(text)
    return dsid


PROMPT = """# Production incident triage task  (task id: {dsid})

## Alert
{alert}

## Your task
Find the most likely root cause **with evidence and a confidence level**, propose a **safe remediation**, and open a
ticket. **Change nothing in production.** Remediation is proposed as text only; nothing is executed.

## Access (read-only)
- Telemetry API (GET only): `$TRIAGE_API_URL`  token: `$TRIAGE_DATA_TOKEN` (expires after 5 minutes; renew with `GET /v1/auth/renew`)
- Helper CLI (same API): `python3 client/triage_cli.py --help`
- Ticket sandbox: `$TRIAGE_TICKET_URL`  token: `$TRIAGE_TICKET_TOKEN`  (use `dedupe_key = {dsid}`; create at most one ticket)
- API reference: `docs/api.md`

## Output
Write your findings to `result.json` following `docs/result_schema.json`
(ranked hypotheses with confidence, evidence cited by ItemId, remediation, ticket id).
{extras}"""


def render_prompt(dsid, ds):
    extras = ""
    for fname, text in sorted(ds.inputs.items()):
        if fname.startswith("operator_"):
            continue
        extras += f"\n## Additional input: `{fname}`\n```\n{text.strip()}\n```\n"
    return PROMPT.format(dsid=dsid, alert=ds.alert["text"], extras=extras)


def build_all(lines=None, include_optional=True, **kw):
    names = BASE_NAMES + CANARY_NAMES + HOLDOUT_NAMES + (OPTIONAL_NAMES if include_optional else [])
    index = {}
    for n in names:
        index[n] = build(n, lines, **kw)
    out_keys = Path(kw.get("out_keys") or common.KEYS)
    out_keys.mkdir(parents=True, exist_ok=True)
    (out_keys / "index.json").write_text(json.dumps(
        {"datasets": index, "kinds": {n: catalog()[n]["kind"] for n in names}}, indent=1, sort_keys=True))
    return index
