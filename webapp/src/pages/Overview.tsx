import { Link } from "react-router-dom";
import { Overview, PlanRow, useApi } from "../api";
import { Bar, ErrorBox, Loading, Page, Stat } from "../components";
import { pct } from "../format";

const SERVICES: [keyof Overview["services"], string, string][] = [
  ["telemetry_api", "Telemetry API", ":8081"], ["ticket_sandbox", "Ticket sandbox", ":8082"], ["postgres", "Postgres", ":5433"],
];

export default function OverviewPage() {
  const o = useApi<Overview>("/overview");
  const p = useApi<{ rows: PlanRow[]; total: number }>("/plan");
  if (o.error) return <ErrorBox error={o.error} />;
  if (!o.data) return <Loading />;
  const d = o.data;
  const rows = p.data?.rows ?? [];
  return (
    <Page title="Overview" sub="Production-incident triage evaluation: Factory vs Cognition vs Cursor">
      {d.config_changed_after_lock && <div className="banner bad" role="alert">config/eval.json changed after it was locked. Thresholds and weights must not change after the first run.</div>}
      {!d.config_locked && <div className="banner warn">Thresholds are not locked yet. Review config/eval.json, then run <code>python -m triage_eval lock</code> before the first run.</div>}
      <div className="grid g4">
        <Stat n={`${d.submitted}/${d.planned_runs || "—"}`} l="Runs submitted / planned" />
        <Stat n={d.started} l="Runs started" />
        <Stat n={d.scored} l="Runs scored" />
        <Stat n={d.integrity_failures} l="Integrity failures" tone={d.integrity_failures ? "bad" : undefined} />
      </div>
      <div className="grid g2" style={{ marginTop: 16 }}>
        <div className="card">
          <h2>Services</h2>
          {SERVICES.map(([k, label, port]) => (
            <div key={k} className="row" style={{ marginBottom: 6 }}>
              <span className={`dot ${d.services[k] ? "up" : ""}`} /> <strong>{label}</strong> <span className="muted mono">{port}</span>
              <span className="spacer" /><span className="muted">{d.services[k] ? "up" : "not reachable"}</span>
            </div>
          ))}
          <p className="muted" style={{ marginBottom: 0, marginTop: 10 }}>Start them with <code>docker compose up -d</code>.</p>
        </div>
        <div className="card">
          <h2>Progress by tool</h2>
          {d.tools.map((t) => {
            const mine = rows.filter((r) => r.tool === t);
            const done = mine.filter((r) => r.status === "submitted").length;
            const s = d.by_tool[t];
            return (
              <div key={t} style={{ marginBottom: 12 }}>
                <div className="row"><strong style={{ textTransform: "capitalize" }}>{t}</strong><span className="spacer" />
                  <span className="muted">{done}/{mine.length || "—"} runs{s ? ` · mean ${s.mean}` : ""}</span></div>
                <Bar value={pct(done, mine.length)} />
              </div>
            );
          })}
        </div>
      </div>
      <div className="card">
        <h2>Next steps</h2>
        <ol style={{ margin: 0, paddingLeft: 18 }}>
          <li><Link to="/new">Start a run</Link> (or pick one from the <Link to="/plan">plan</Link>), give the tool only its workspace folder.</li>
          <li>Paste or collect <code>result.json</code>, then finish the run to record cost and interventions.</li>
          <li>Score blind in <Link to="/review">Blind review</Link>, then read the <Link to="/report">Report</Link> against the pass criteria.</li>
        </ol>
      </div>
    </Page>
  );
}
