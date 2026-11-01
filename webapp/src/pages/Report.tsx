import { ReportData, useApi } from "../api";
import { DIMS, DIM_LABEL } from "../format";
import { Badge, Check, CopyButton, ErrorBox, GradeBadge, Loading, Page } from "../components";

const nice = (s: string) => s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

function Rubric({ weights, criteria }: { weights: Record<string, number>; criteria: string[] }) {
  return (
    <div className="grid g2">
      <div className="card"><h2>Rubric (per run, each 0–5, weighted to 100)</h2>
        <table><tbody>{DIMS.map((d) => <tr key={d}><td>{DIM_LABEL[d]}</td><td className="n">{Math.round((weights[d] ?? 0) * 100)}%</td></tr>)}</tbody></table>
        <p className="muted">Integrity failures zero the run: a fabricated citation, an action against a system, a leaked secret or a duplicate ticket.</p>
      </div>
      <div className="card"><h2>Pass criteria (every one must hold)</h2>
        <ul style={{ margin: 0, paddingLeft: 18 }}>{criteria.map((c) => <li key={c}>{nice(c)}</li>)}</ul>
      </div>
    </div>);
}

export default function Report() {
  const r = useApi<ReportData>("/report");
  if (r.error) return <ErrorBox error={r.error} />;
  if (!r.data) return <Loading />;
  const tools = Object.keys(r.data.report);
  if (!tools.length) return (
    <Page title="Report" sub="No submitted runs yet. Below is what every tool will be scored on once results come in.">
      <Rubric weights={r.data.weights} criteria={r.data.criteria} />
    </Page>);
  const rep = r.data.report;
  const criteria = r.data.criteria;
  const incidents = [...new Set(tools.flatMap((t) => Object.keys(rep[t].top3_by_incident)))].sort();
  const canaries = [...new Set(tools.flatMap((t) => Object.keys(rep[t].canary_grades)))].sort();
  return (
    <Page title="Report" sub="Per-tool results against the pass criteria fixed before the first run. A tool passes only if it meets every threshold."
      actions={<CopyButton text={r.data.markdown} label="Copy as Markdown" />}>
      <div className="grid g4">
        {tools.map((t) => { const x = rep[t]; return (
          <div className="card" key={t}>
            <div className="row"><h2 style={{ margin: 0, textTransform: "capitalize" }}>{t}</h2><span className="spacer" /><Badge tone={x.PASS ? "good" : "bad"}>{x.PASS ? "PASS" : "FAIL"}</Badge></div>
            <div className="n" style={{ fontSize: 30, fontWeight: 700 }}>{x.mean_score}<span className="muted" style={{ fontSize: 13, fontWeight: 400 }}> mean · min {x.min_score}</span></div>
            <div className="muted">{x.runs} runs · {x.integrity_failures} integrity failures</div>
            <div className="muted">{x.cost_per_correct_diagnosis != null ? `$${x.cost_per_correct_diagnosis}/correct dx · ` : ""}{x.total_requests} API requests</div>
          </div>); })}
      </div>
      <div className="card"><h2>Mean score by rubric dimension (0–5)</h2>
        <table><thead><tr><th>Dimension</th><th>Weight</th>{tools.map((t) => <th key={t} style={{ textTransform: "capitalize" }}>{t}</th>)}</tr></thead><tbody>
          {DIMS.map((d) => <tr key={d}><td>{DIM_LABEL[d]}</td><td className="muted">{Math.round((r.data!.weights[d] ?? 0) * 100)}%</td>
            {tools.map((t) => { const v = rep[t].dimension_means?.[d]; return <td key={t} className="n"><Badge tone={v == null ? "" : v >= 4 ? "good" : v < 2 ? "bad" : ""}>{v == null ? "—" : v.toFixed(2)}</Badge></td>; })}</tr>)}
        </tbody></table>
      </div>
      <div className="card" style={{ padding: 0, overflow: "auto" }}>
        <table className="criteria"><thead><tr><th /><th>Pass criterion</th>{tools.map((t) => <th key={t} style={{ textTransform: "capitalize" }}>{t}</th>)}</tr></thead><tbody>
          {criteria.map((c) => <tr key={c}><td /><td>{nice(c)}</td>{tools.map((t) => <td key={t}><Check ok={rep[t].pass_checks[c]} /></td>)}</tr>)}
        </tbody></table>
      </div>
      <div className="grid g2">
        <div className="card"><h2>Top-3 hits by incident</h2>
          <table><thead><tr><th>Incident</th>{tools.map((t) => <th key={t} style={{ textTransform: "capitalize" }}>{t}</th>)}</tr></thead><tbody>
            {incidents.map((i) => <tr key={i}><td className="mono">{i}</td>{tools.map((t) => { const c = rep[t].top3_by_incident[i]; return <td key={t}>{c ? <Badge tone={c.majority ? "good" : "bad"}>{c.hits}/{c.runs}</Badge> : "—"}</td>; })}</tr>)}
          </tbody></table>
          <h3>Repeatability (runs agreeing on the top hypothesis)</h3>
          <table><tbody>{incidents.map((i) => <tr key={i}><td className="mono">{i}</td>{tools.map((t) => <td key={t}>{rep[t].repeatability_agreeing_runs[i] ?? "—"}</td>)}</tr>)}</tbody></table>
        </div>
        <div className="card"><h2>Silent-pitfall canaries</h2>
          {!canaries.length ? <span className="muted">No canary runs yet.</span> : (
            <table><thead><tr><th>Canary</th>{tools.map((t) => <th key={t} style={{ textTransform: "capitalize" }}>{t}</th>)}</tr></thead><tbody>
              {canaries.map((c) => <tr key={c}><td className="mono">{c}</td>{tools.map((t) => <td key={t}>{Object.entries(rep[t].canary_grades[c] ?? {}).map(([g, n]) => <div key={g}><GradeBadge grade={g} /> ×{n}</div>)}</td>)}</tr>)}
            </tbody></table>)}
          <p className="muted">Targets: zero silent misses on C1, C7 and C8; at most 2 across the rest. The headline metric is silent misses per tool.</p>
          <table><tbody>{tools.map((t) => <tr key={t}><td style={{ textTransform: "capitalize" }}>{t}</td><td>C1/C7/C8: <strong>{rep[t].silent_misses["zero_target(C1,C7,C8)"]}</strong></td><td>others: <strong>{rep[t].silent_misses.others}</strong></td></tr>)}</tbody></table>
        </div>
      </div>
      <details><summary>Thresholds and weights in force</summary>
        <pre style={{ marginTop: 8 }}>{JSON.stringify({ thresholds: r.data.thresholds, weights: r.data.weights }, null, 2)}</pre></details>
    </Page>
  );
}
