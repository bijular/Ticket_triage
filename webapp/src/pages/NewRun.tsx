import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Dataset, Overview, api, useApi } from "../api";
import { CopyButton, ErrorBox, Loading, Page } from "../components";
import { kindLabel } from "../format";

interface Created { run_id: string; workspace: string; env: Record<string, string>; note: string }

export default function NewRun() {
  const [sp] = useSearchParams();
  const ds = useApi<Dataset[]>("/datasets");
  const ov = useApi<Overview>("/overview");
  const [tool, setTool] = useState(sp.get("tool") ?? "factory");
  const [dataset, setDataset] = useState(sp.get("dataset") ?? "inc01");
  const [rep, setRep] = useState(Number(sp.get("rep") ?? 1));
  const [model, setModel] = useState("");
  const [pinned, setPinned] = useState(false);
  const [withPg, setWithPg] = useState(false);
  const [host, setHost] = useState("127.0.0.1");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [out, setOut] = useState<Created | null>(null);
  if (ds.error) return <ErrorBox error={ds.error} />;
  if (!ds.data) return <Loading />;
  const chosen = ds.data.find((d) => d.name === dataset);
  const submit = async () => {
    setBusy(true); setErr("");
    try {
      setOut(await api<Created>("/runs", { method: "POST", body: JSON.stringify({ tool, dataset, replicate: rep, model, model_pinned: pinned, with_pg: withPg }) }));
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const exports = out ? [
    `export TRIAGE_API_URL=http://${host}:8081`, `export TRIAGE_TICKET_URL=http://${host}:8082`,
    ...Object.entries(out.env).map(([k, v]) => `export ${k}=${v}`)].join("\n") : "";
  return (
    <Page title="New run" sub="Creates the run folder, an isolated workspace and scoped tokens. Re-using a run id reuses its ticket sandbox (exercises duplicate-ticket behaviour).">
      <div className="cols">
        <div className="card">
          <label className="f"><span>Tool</span><select value={tool} onChange={(e) => setTool(e.target.value)}>{(ov.data?.tools ?? ["factory", "cognition", "cursor"]).map((t) => <option key={t}>{t}</option>)}</select></label>
          <label className="f"><span>Dataset</span>
            <select value={dataset} onChange={(e) => setDataset(e.target.value)}>
              {["incident", "canary", "canary_optional", "holdout"].map((k) => (
                <optgroup key={k} label={kindLabel(k)}>{ds.data!.filter((d) => d.kind === k).map((d) => <option key={d.name} value={d.name}>{d.name}{d.canary ? ` (${d.canary})` : ""}</option>)}</optgroup>))}
            </select></label>
          {chosen && <p className="muted" style={{ marginTop: -6 }}>Alert the tool will see: “{chosen.alert}”</p>}
          <div className="row">
            <label className="f" style={{ flex: 1 }}><span>Replicate</span><input type="number" min={1} max={9} value={rep} onChange={(e) => setRep(Number(e.target.value))} /></label>
            <label className="f" style={{ flex: 2 }}><span>Model (record on every run — C11)</span><input value={model} onChange={(e) => setModel(e.target.value)} placeholder="e.g. as shown in the tool's settings" /></label>
          </div>
          <label className="row" style={{ marginBottom: 8 }}><input type="checkbox" checked={pinned} onChange={(e) => setPinned(e.target.checked)} /> Model is pinned (tool allows pinning)</label>
          <label className="row" style={{ marginBottom: 14 }}><input type="checkbox" checked={withPg} onChange={(e) => setWithPg(e.target.checked)} /> Also create a read-only Postgres role (needs <code>docker compose up -d</code> and <code>pg-seed</code>)</label>
          <label className="f"><span>Host the tool will use to reach the services (use a reachable name for cloud agents)</span><input value={host} onChange={(e) => setHost(e.target.value)} /></label>
          {err && <div className="banner bad" role="alert">{err}</div>}
          <button className="primary" disabled={busy} onClick={submit}>{busy ? "Creating…" : "Create run"}</button>
        </div>
        <div>
          {!out ? <div className="card muted">Tokens appear here after creating the run. The data and ticket tokens expire after 5 minutes and can be renewed with <code>GET /v1/auth/renew</code>.</div> : (
            <div className="card">
              <h2>Run created · <Link to={`/runs/${out.run_id}`}>{out.run_id}</Link></h2>
              <div className="banner warn">Give the tool <strong>only</strong> this folder — never the repo root (it holds the answer keys).</div>
              <div className="row"><code className="mono" style={{ wordBreak: "break-all" }}>{out.workspace}</code><CopyButton text={out.workspace} /></div>
              <h3>Environment for the tool</h3>
              <pre>{exports}</pre>
              <div className="row" style={{ marginTop: 8 }}><CopyButton text={exports} label="Copy exports" /><span className="muted">Contains live tokens — localhost use only.</span></div>
              <p className="muted">{out.note} Collect <code>result.json</code> from the workspace, then finish the run.</p>
            </div>)}
        </div>
      </div>
    </Page>
  );
}
