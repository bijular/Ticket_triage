#!/usr/bin/env python3
"""Thin, dependency-free client for the read-only telemetry API and the ticket sandbox.

Identical for every tool under test. It does NOT retry, paginate or hide errors: it prints the
HTTP status and JSON body and exits non-zero on HTTP >= 400, so retry/pagination behaviour is the
tool's own (and is measured from the server-side request log).

Env: TRIAGE_API_URL (default http://127.0.0.1:8081), TRIAGE_DATA_TOKEN,
     TRIAGE_TICKET_URL (default http://127.0.0.1:8082), TRIAGE_TICKET_TOKEN
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def call(method, base, token, path, params=None, body=None):
    url = base.rstrip("/") + path + ("?" + urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None}) if params else "")
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    try:
        data = json.loads(raw)
    except Exception:
        data = {"raw": raw.decode(errors="replace")}
    print(json.dumps({"http_status": status, "body": data}, indent=1))
    sys.exit(0 if status < 400 else 1)


def main():
    api = os.environ.get("TRIAGE_API_URL", "http://127.0.0.1:8081")
    tok = os.environ.get("TRIAGE_DATA_TOKEN", "")
    tk_url = os.environ.get("TRIAGE_TICKET_URL", "http://127.0.0.1:8082")
    tk_tok = os.environ.get("TRIAGE_TICKET_TOKEN", "")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="c", required=True)
    for name in ("alert", "services", "deploys", "schema", "runbooks", "metric-names", "renew"):
        p = sp.add_parser(name)
        if name == "deploys":
            p.add_argument("--service")
    p = sp.add_parser("logs"); [p.add_argument(f"--{x}") for x in ("start", "end", "service", "severity", "q", "operation_Id", "limit", "order", "page_token")]
    p = sp.add_parser("traces"); [p.add_argument(f"--{x}") for x in ("start", "end", "service", "target", "q", "operation_Id", "limit", "failed", "page_token")]
    p = sp.add_parser("metrics"); [p.add_argument(f"--{x}") for x in ("service", "metric", "start", "end", "interval")]
    p = sp.add_parser("slow-queries"); p.add_argument("--snapshot")
    p = sp.add_parser("explain"); p.add_argument("--query_id", required=True); p.add_argument("--snapshot")
    p = sp.add_parser("jira"); p.add_argument("key")
    p = sp.add_parser("runbook"); p.add_argument("name")
    p = sp.add_parser("ticket-create"); [p.add_argument(f"--{x}", required=x in ("title", "dedupe_key")) for x in ("title", "description", "dedupe_key", "severity", "proposed_remediation")]
    p.add_argument("--evidence", nargs="*", default=[])
    p = sp.add_parser("ticket-list"); p.add_argument("--dedupe_key")
    p = sp.add_parser("ticket-comment"); p.add_argument("key"); p.add_argument("body")
    a = ap.parse_args()
    v = {k: x for k, x in vars(a).items() if k != "c" and x is not None}
    simple = {"alert": "/v1/alert", "services": "/v1/services", "deploys": "/v1/deploys", "schema": "/v1/db/schema",
              "runbooks": "/v1/runbooks", "metric-names": "/v1/metrics/names", "renew": "/v1/auth/renew",
              "logs": "/v1/logs", "traces": "/v1/traces", "metrics": "/v1/metrics", "slow-queries": "/v1/db/slow-queries",
              "explain": "/v1/db/explain"}
    if a.c in simple:
        call("GET", api, tok, simple[a.c], v)
    elif a.c == "jira":
        call("GET", api, tok, f"/v1/jira/issues/{a.key}")
    elif a.c == "runbook":
        call("GET", api, tok, f"/v1/runbooks/{a.name}")
    elif a.c == "ticket-create":
        body = {"title": a.title, "description": a.description or "", "dedupe_key": a.dedupe_key,
                "severity": a.severity or "Sev3", "proposed_remediation": a.proposed_remediation or "",
                "evidence_refs": a.evidence}
        call("POST", tk_url, tk_tok, "/v1/tickets", body=body)
    elif a.c == "ticket-list":
        call("GET", tk_url, tk_tok, "/v1/tickets", v)
    elif a.c == "ticket-comment":
        call("POST", tk_url, tk_tok, f"/v1/tickets/{a.key}/comments", body={"body": a.body})


if __name__ == "__main__":
    main()
