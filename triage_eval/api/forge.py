"""GitHub / GitHub Actions / Azure DevOps shaped views over a dataset's deploy history (Tier A).

Everything is derived from deploys.json, so facts stay consistent with the answer key (the culprit
deploy is the culprit commit/PR/run/build). Shapes follow the public REST APIs closely enough for a
tool's generic HTTP/CLI usage; they are NOT byte-compatible clones (e.g. Actions logs come back as
JSON, not a zip). Timestamps follow deploys.json: in the C3 overlay the whole deploy-history family
stays in naive IST on purpose.
"""
import datetime as dt
import hashlib
import re

ORG = "acme"
IST = dt.timedelta(hours=5, minutes=30)


def _num(d):
    return int(d["id"].split("-")[1])


def _sha(d):
    return hashlib.sha1(d["sha"].encode()).hexdigest()[:33] + d["sha"]       # 40 hex chars, starts with a stable prefix


def _t(d, minutes=0):
    """Shift deploys.json's deployed_at by `minutes`, preserving its format (Z or naive IST)."""
    s = d["deployed_at"]
    if s.endswith("IST"):
        t = dt.datetime.strptime(s[:-4], "%Y-%m-%d %H:%M:%S") + dt.timedelta(minutes=minutes)
        return t.strftime("%Y-%m-%d %H:%M:%S") + " IST"
    t = dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ") + dt.timedelta(minutes=minutes)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def _files(d):
    m = re.search(r"^\+\+\+ b/(\S+)", d["diff"], re.M)
    return [{"filename": m.group(1) if m else f"{d['repo']}/CHANGE", "status": "modified", "patch": d["diff"]}]


def _commit(d, full=False):
    c = {"sha": _sha(d), "commit": {"message": d["message"], "author": {"name": d["author"], "date": _t(d, -8)}},
         "author": {"login": d["author"]}, "html_url": f"https://github.example/{ORG}/{d['repo']}/commit/{d['sha']}"}
    if full:
        c["files"] = _files(d)
    return c


def _pr(d):
    head, _, body = d["message"].partition("\n")
    return {"number": _num(d), "title": head, "body": body.strip(), "state": "closed", "user": {"login": d["author"]},
            "created_at": _t(d, -30), "merged_at": _t(d, -8), "merge_commit_sha": _sha(d),
            "head": {"ref": f"change/{d['id']}"}, "base": {"ref": "main"}, "changed_files": 1}


def _run(d, attempt=1):
    flaky = d["id"] == "deploy-4817"      # decoy: a CI flake that was re-run (unrelated to any incident)
    ok = not (flaky and attempt == 1)
    return {"id": 7700000000 + _num(d) * 10 + attempt - 1, "name": f"deploy-{d['service']}", "event": "push",
            "head_sha": _sha(d), "run_attempt": attempt, "status": "completed",
            "conclusion": "success" if ok else "failure", "created_at": _t(d, -6 + attempt), "updated_at": _t(d, 0),
            "head_commit": {"message": d["message"].split("\n")[0]}, "actor": {"login": d["author"]}}


def _runs(d):
    return [_run(d, 1), _run(d, 2)] if d["id"] == "deploy-4817" else [_run(d)]


def _jobs(run, d):
    bad = run["conclusion"] == "failure"
    steps = [("checkout", "success"), ("unit tests", "failure" if bad else "success"),
             ("build image", "skipped" if bad else "success"), ("deploy to prod", "skipped" if bad else "success")]
    return [{"id": run["id"] * 10 + 1, "run_id": run["id"], "name": "ci-deploy", "conclusion": run["conclusion"],
             "started_at": run["created_at"], "completed_at": run["updated_at"],
             "steps": [{"name": n, "conclusion": c, "number": i + 1} for i, (n, c) in enumerate(steps)]}]


def _log(run, d):
    bad = run["conclusion"] == "failure"
    lines = [f"{run['created_at']} ##[group]Run actions/checkout", f"{run['created_at']} checked out {d['sha']}",
             f"{run['created_at']} ##[group]unit tests"]
    lines += ["FAILED tests/test_templates.py::test_footer_render - flaky timeout (1 failed, 212 passed)"] if bad else \
             ["212 passed in 41.2s"]
    if not bad:
        lines += [f"{run['updated_at']} ##[group]deploy to prod",
                  f"{run['updated_at']} applying change to {d['service']}: {d['message'].splitlines()[0]}",
                  f"{run['updated_at']} rollout complete, deployment {d['id']} recorded"]
    return "\n".join(lines)


def _paged(items, q, key=None):
    pp = min(int(q.get("per_page", 30)), 100)
    pg = max(int(q.get("page", 1)), 1)
    return items[(pg - 1) * pp: pg * pp]


def handle(rp, path, q):
    """Return (status, body) for /github/... or /ado/... paths, or None if not a forge path."""
    ds = rp.deploys
    parts = [p for p in path.split("/") if p]
    if parts[:1] == ["github"]:
        if parts == ["github", "repos"]:
            return 200, [{"full_name": f"{ORG}/{r}", "name": r} for r in sorted({d["repo"] for d in ds})]
        if len(parts) >= 5 and parts[1] == "repos":
            repo = parts[3]
            mine = [d for d in ds if d["repo"] == repo]
            rest = parts[4:]
            if not mine and rest:
                return 404, {"message": "Not Found"}
            if rest == ["commits"]:
                return 200, _paged([_commit(d) for d in reversed(mine)], q)
            if len(rest) == 2 and rest[0] == "commits":
                d = next((x for x in mine if _sha(x).startswith(rest[1]) or x["sha"] == rest[1]), None)
                return (200, _commit(d, True)) if d else (404, {"message": "No commit found for SHA"})
            if rest == ["pulls"]:
                return 200, _paged([_pr(d) for d in reversed(mine)], q)
            if len(rest) == 2 and rest[0] == "pulls":
                d = next((x for x in mine if str(_num(x)) == rest[1]), None)
                return (200, {**_pr(d), "files": _files(d)}) if d else (404, {"message": "Not Found"})
            if rest == ["actions", "runs"]:
                runs = [r for d in reversed(mine) for r in _runs(d)]
                return 200, {"total_count": len(runs), "workflow_runs": _paged(runs, q)}
            if len(rest) >= 3 and rest[:2] == ["actions", "runs"]:
                for d in mine:
                    for r in _runs(d):
                        if str(r["id"]) == rest[2]:
                            if len(rest) == 3:
                                return 200, r
                            if rest[3] == "jobs":
                                return 200, {"total_count": 1, "jobs": _jobs(r, d)}
                            if rest[3] == "logs":
                                return 200, {"note": "real GitHub returns a zip; this mock returns text", "log": _log(r, d)}
                return 404, {"message": "Not Found"}
        return 404, {"message": "Not Found", "documentation_url": "see docs/api.md"}
    if parts[:1] == ["ado"] and len(parts) >= 5 and parts[3] == "_apis":
        api = parts[4:]
        if api[:2] == ["build", "builds"] and len(api) == 2:
            v = [{"id": 51000 + _num(d), "buildNumber": f"{d['service']}-{_num(d)}", "status": "completed",
                  "result": "succeeded", "queueTime": _t(d, -6), "finishTime": _t(d, 0), "sourceVersion": _sha(d),
                  "definition": {"name": f"{d['service']}-ci"}, "repository": {"name": d["repo"]},
                  "requestedFor": {"displayName": d["author"]}, "reason": "individualCI"} for d in reversed(ds)]
            return 200, {"count": len(v), "value": v}
        if api[:2] == ["release", "releases"] and len(api) == 2:
            v = [{"id": _num(d), "name": f"Release-{_num(d)}", "createdOn": _t(d, 0), "modifiedBy": {"displayName": d["author"]},
                  "description": d["message"], "releaseDefinition": {"name": d["service"]},
                  "environments": [{"name": "prod", "status": "succeeded", "deploySteps": [{"status": "succeeded", "queuedOn": _t(d, -1)}]}]}
                 for d in reversed(ds)]
            return 200, {"count": len(v), "value": v}
        if len(api) >= 3 and api[:2] == ["git", "repositories"]:
            repo = api[2]
            mine = [d for d in ds if d["repo"] == repo]
            if len(api) == 4 and api[3] == "commits":
                v = [{"commitId": _sha(d), "author": {"name": d["author"], "date": _t(d, -8)}, "comment": d["message"]} for d in reversed(mine)]
                return 200, {"count": len(v), "value": v}
            if len(api) == 5 and api[3] == "commits":
                d = next((x for x in mine if _sha(x).startswith(api[4]) or x["sha"] == api[4]), None)
                if not d:
                    return 404, {"message": "commit not found"}
                return 200, {"commitId": _sha(d), "comment": d["message"], "author": {"name": d["author"], "date": _t(d, -8)},
                             "changes": [{"item": {"path": f["filename"]}, "changeType": "edit", "diff": f["patch"]} for f in _files(d)]}
        return 404, {"message": "unknown ADO endpoint"}
    return None
