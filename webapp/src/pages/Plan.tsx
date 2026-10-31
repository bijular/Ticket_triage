import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Dataset, PlanRow, useApi } from "../api";
import { Badge, ErrorBox, Loading, Page, StatusBadge } from "../components";
import { kindLabel } from "../format";

export default function Plan() {
  const plan = useApi<{ rows: PlanRow[]; total: number; submitted: number }>("/plan");
  const ds = useApi<Dataset[]>("/datasets");
  const nav = useNavigate();
  const [tool, setTool] = useState("");
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const kinds = useMemo(() => Object.fromEntries((ds.data ?? []).map((d) => [d.name, d.kind])), [ds.data]);
  const rows = useMemo(
    () => (plan.data?.rows ?? []).filter((r) => (!tool || r.tool === tool) && (!status || r.status === status) && (!kind || kinds[r.name ?? ""] === kind)),
    [plan.data, tool, status, kind, kinds],
  );
  if (plan.error) return <ErrorBox error={plan.error} />;
  if (!plan.data) return <Loading />;
  if (!plan.data.total)
    return <Page title="Run plan"><div className="card empty">No plan yet. Run <code>python -m triage_eval plan</code> to create plan.csv (171 runs).</div></Page>;
  return (
    <Page title="Run plan" sub={`${plan.data.submitted} of ${plan.data.total} planned runs submitted. Order is randomised to limit drift and novelty effects.`}>
      <div className="filters">
        <select aria-label="Tool" value={tool} onChange={(e) => setTool(e.target.value)}><option value="">All tools</option><option>factory</option><option>cognition</option><option>cursor</option></select>
        <select aria-label="Kind" value={kind} onChange={(e) => setKind(e.target.value)}><option value="">All kinds</option><option value="incident">Incident</option><option value="canary">Canary</option><option value="holdout">Hold-out</option></select>
        <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Any status</option><option>planned</option><option>started</option><option>submitted</option></select>
        <span className="muted" style={{ alignSelf: "center" }}>{rows.length} runs</span>
      </div>
      <div className="card" style={{ padding: 0, overflow: "auto" }}>
        <table>
          <thead><tr><th>Run</th><th>Tool</th><th>Dataset</th><th>Kind</th><th>Rep</th><th>Mode</th><th>Status</th><th /></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.run_id}>
                <td className="mono">{r.status === "planned" ? r.run_id : <Link to={`/runs/${r.run_id}`}>{r.run_id}</Link>}</td>
                <td style={{ textTransform: "capitalize" }}>{r.tool}</td><td className="mono">{r.name}</td>
                <td><Badge>{kindLabel(kinds[r.name ?? ""])}</Badge></td><td>{r.replicate}</td><td className="muted">{r.mode}</td>
                <td><StatusBadge status={r.status} /></td>
                <td className="right">{r.status === "planned" && (
                  <button onClick={() => nav(`/new?tool=${r.tool}&dataset=${r.name}&rep=${r.replicate}`)}>Start</button>)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Page>
  );
}
