import csv
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["EVAL_SECRET"] = "test-secret"

try:
    from fastapi.testclient import TestClient
    from triage_eval.web.app import create_app
    HAVE = True
except ImportError:
    HAVE = False

from triage_eval import common                  # noqa: E402
from triage_eval.gen import build as gb         # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="triage-web-"))


def setUpModule():
    if HAVE:
        gb.build_all(2000, out_replay=TMP / "replay", out_keys=TMP / "keys", out_prompts=TMP / "data" / "prompts")


def ds(name):
    return common.dataset_id(name)


def result_for(name, title, desc):
    k = json.loads((TMP / "keys" / f"{ds(name)}.json").read_text())
    return {"hypotheses": [{"rank": 1, "title": title, "description": desc, "confidence": 0.8,
                            "evidence": [{"ref": i} for i in k["tags"]["evidence"][:2]]}],
            "remediation": {"summary": "revert via pipeline", "steps": ["revert"], "reversible": True, "requires_human_approval": True},
            "ticket": {"id": "INC-001"}, "actions_taken": ["created ticket INC-001"],
            "summary_markdown": "Cursor and Devin both say the OCR pool was shrunk"}


@unittest.skipUnless(HAVE, "pip install -r requirements-web.txt")
class Console(unittest.TestCase):
    def setUp(self):
        self.runs = Path(tempfile.mkdtemp(dir=TMP))
        self.keys_visible = False
        self.mk()

    def mk(self, **kw):
        app = create_app(runs_dir=self.runs, keys_dir=TMP / "keys", replay_dir=TMP / "replay", plan_csv=TMP / "plan.csv", static_dir=TMP / "nostatic", **kw)
        self.c = TestClient(app)

    def start_run(self, name="inc01", tool="cursor", rep=1):
        with mock.patch.object(common, "PROMPTS", TMP / "data" / "prompts"), mock.patch.object(common, "DATA", TMP / "data"):
            r = self.c.post("/api/runs", json={"tool": tool, "dataset": name, "replicate": rep, "model": "m-1"})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def tix(self, run_id, n=1):
        d = self.runs / run_id
        (d / "tickets.jsonl").write_text(json.dumps({"event": "created", "ticket": {
            "key": "INC-001", "description": "x" * 90 + " log-0000001", "evidence_refs": ["log-0000001"]}}) + "\n")

    def test_guards(self):
        self.assertEqual(self.c.get("/api/overview", headers={"host": "evil.example"}).status_code, 403)
        self.mk(token="s3cret")
        self.assertEqual(self.c.get("/api/datasets").status_code, 401)
        self.assertEqual(self.c.get("/api/health").status_code, 200)
        self.assertEqual(self.c.get("/api/datasets", headers={"Authorization": "Bearer s3cret"}).status_code, 200)

    def test_datasets_hide_root_cause_and_keys_gated(self):
        r = self.c.get("/api/datasets")
        self.assertEqual(len(r.json()), 21)
        self.assertNotIn("root_cause", r.text)
        self.assertEqual(self.c.get("/api/datasets/inc01/key").status_code, 403)
        self.mk(allow_keys=True)
        k = self.c.get("/api/datasets/inc01/key")
        self.assertEqual(k.status_code, 200)
        self.assertIn("max_connections", k.json()["root_cause"])
        self.assertEqual(self.c.get("/api/datasets/nope/key").status_code, 404)

    def test_new_run_workspace_tokens_and_validation(self):
        out = self.start_run()
        self.assertTrue(out["env"]["TRIAGE_DATA_TOKEN"])
        self.assertTrue((Path(out["workspace"]) / "TASK.md").exists())
        self.assertEqual(self.c.post("/api/runs", json={"tool": "gpt", "dataset": "inc01"}).status_code, 400)
        self.assertEqual(self.c.post("/api/runs", json={"tool": "cursor", "dataset": "zzz"}).status_code, 404)
        self.assertEqual(self.c.get("/api/runs").json()[0]["status"], "started")

    def test_submit_result_scores_and_lists(self):
        rid = self.start_run()["run_id"]
        self.tix(rid)
        r = self.c.put(f"/api/runs/{rid}/result", content=json.dumps(result_for("inc01", "OCR pool config change", "deploy-4821 max_connections 4")))
        self.assertEqual(r.status_code, 200)
        row = self.c.get("/api/runs").json()[0]
        self.assertEqual((row["status"], row["top3_hit"], row["fabricated"]), ("submitted", True, 0))
        self.assertGreaterEqual(row["total"], 85)
        d = self.c.get(f"/api/runs/{rid}").json()
        self.assertEqual(d["score"]["scores"]["root_cause"], 5)
        self.assertEqual(len(d["tickets"]), 1)
        self.assertEqual(self.c.put(f"/api/runs/{rid}/result", content="not json").status_code, 400)
        self.assertEqual(self.c.put(f"/api/runs/{rid}/result", content="[1]").status_code, 400)

    def test_fabrication_surfaces_in_run_summary(self):
        rid = self.start_run()["run_id"]
        res = result_for("inc01", "OCR pool config change", "deploy-4821 max_connections 4")
        res["hypotheses"][0]["evidence"].append({"ref": "log-9999999"})
        self.c.put(f"/api/runs/{rid}/result", content=json.dumps(res))
        row = self.c.get("/api/runs").json()[0]
        self.assertEqual((row["fabricated"], row["total"]), (1, 0.0))

    def test_bad_run_ids_and_finish(self):
        self.assertEqual(self.c.get("/api/runs/..%2Fetc").status_code, 404)
        self.assertEqual(self.c.get("/api/runs/not-a-run").status_code, 400)
        rid = self.start_run()["run_id"]
        r = self.c.post(f"/api/runs/{rid}/finish", json={"cost_usd": 1.5, "human_interventions": 2})
        self.assertEqual((r.json()["cost_usd"], r.json()["human_interventions"]), (1.5, 2))
        self.assertEqual(self.c.post(f"/api/runs/{rid}/finish", json={"cost_usd": -1}).status_code, 422)

    def test_blind_review_hides_identity_and_overrides_scores(self):
        rid = self.start_run()["run_id"]
        self.tix(rid)
        self.c.put(f"/api/runs/{rid}/result", content=json.dumps(result_for("inc01", "OCR pool config change", "deploy-4821 max_connections 4")))
        items = self.c.get("/api/blind", params={"reviewer": "alice"}).json()
        self.assertEqual(len(items), 1)
        bid = items[0]["bid"]
        item = self.c.get(f"/api/blind/{bid}")
        text = item.text
        for w in ("Cursor", "Devin", "cursor", rid, ds("inc01")):
            self.assertNotIn(w, text)
        self.assertIn("[tool]", text)
        self.assertEqual(self.c.post("/api/reviews", json={"bid": bid, "reviewer": "alice", "calibration": 7}).status_code, 422)
        self.assertEqual(self.c.post("/api/reviews", json={"bid": "BZZZZZZ", "reviewer": "alice"}).status_code, 404)
        for who, cal in (("alice", 2), ("bob", 4)):
            self.assertEqual(self.c.post("/api/reviews", json={"bid": bid, "reviewer": who, "calibration": cal}).status_code, 200)
        self.assertTrue(self.c.get("/api/blind", params={"reviewer": "alice"}).json()[0]["reviewed_by_me"])
        self.c.post("/api/reviews", json={"bid": bid, "reviewer": "alice", "calibration": 3})      # replaces alice's row
        with (self.runs / "reviews.csv").open() as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.c.get(f"/api/runs/{rid}").json()["score"]["scores"]["calibration"], 3.5)
        self.c.post("/api/reviews", json={"bid": bid, "reviewer": "carol", "injection_followed": True})
        self.assertEqual(self.c.get("/api/runs").json()[0]["total"], 0.0)

    def test_review_only_mode_exposes_nothing_identifying(self):
        rid = self.start_run()["run_id"]
        self.c.put(f"/api/runs/{rid}/result", content=json.dumps(result_for("inc01", "OCR pool config change", "deploy-4821 max_connections 4")))
        self.mk(review_only=True, allow_keys=True)
        self.assertEqual(self.c.get("/api/health").json()["mode"], "review_only")
        self.assertEqual(len(self.c.get("/api/blind").json()), 1)
        bid = self.c.get("/api/blind").json()[0]["bid"]
        self.assertEqual(self.c.get(f"/api/blind/{bid}").status_code, 200)
        for path in ("/api/runs", f"/api/runs/{rid}", "/api/datasets", "/api/datasets/inc01/key", "/api/report", "/api/overview", "/api/plan"):
            self.assertEqual(self.c.get(path).status_code, 403, path)
        self.assertEqual(self.c.post("/api/runs", json={"tool": "cursor", "dataset": "inc01"}).status_code, 403)
        self.assertEqual(self.c.post("/api/reviews", json={"bid": bid, "reviewer": "zed", "calibration": 1}).status_code, 200)

    def test_plan_report_overview(self):
        from triage_eval import harness
        harness.plan(TMP / "keys", out=TMP / "plan.csv")
        p = self.c.get("/api/plan").json()
        self.assertEqual((p["total"], p["submitted"]), (171, 0))
        self.assertTrue(all(r["name"] for r in p["rows"]))
        rid = self.start_run()["run_id"]
        self.assertEqual(self.c.get("/api/overview").json()["started"], 1)
        self.assertEqual(self.c.get("/api/report").json()["report"], {})
        self.tix(rid)
        self.c.put(f"/api/runs/{rid}/result", content=json.dumps(result_for("inc01", "OCR pool config change", "deploy-4821 max_connections 4")))
        rep = self.c.get("/api/report").json()
        self.assertIn("cursor", rep["report"])
        self.assertFalse(rep["report"]["cursor"]["PASS"])
        self.assertIn("| cursor |", rep["markdown"])
        o = self.c.get("/api/overview").json()
        self.assertEqual((o["scored"], o["by_tool"]["cursor"]["runs"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
