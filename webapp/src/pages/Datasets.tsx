import { useMemo, useState } from "react";
import { Dataset, api, useApi } from "../api";
import { Badge, ErrorBox, Loading, Page } from "../components";
import { kindLabel } from "../format";

export default function Datasets() {
  const ds = useApi<Dataset[]>("/datasets");
  const ov = useApi<{ keys_visible: boolean }>("/overview");
  const [kind, setKind] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [key, setKey] = useState<Record<string, any> | null>(null);
  const [err, setErr] = useState("");
  const rows = useMemo(() => (ds.data ?? []).filter((d) => !kind || d.kind.startsWith(kind)), [ds.data, kind]);
  if (ds.error) return <ErrorBox error={ds.error} />;
  if (!ds.data) return <Loading />;
  const show = async (name: string) => {
    setOpen(name); setKey(null); setErr("");
    try { setKey(await api(`/datasets/${name}/key`)); } catch (e: any) { setErr(e.message); }
  };
  return (
    <Page title="Datasets" sub="Opaque ids are what tools see. Root causes stay in the private answer keys.">
      <div className="filters">
        <select aria-label="Kind" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">All</option><option value="incident">Incidents</option><option value="canary">Canaries</option><option value="holdout">Hold-outs</option>
        </select>
        <span className="muted" style={{ alignSelf: "center" }}>{rows.length} datasets</span>
      </div>
      <div className="card" style={{ padding: 0, overflow: "auto" }}>
        <table>
          <thead><tr><th>Name</th><th>Id</th><th>Kind</th><th>Scenario</th><th>Canary / what a pass looks like</th><th className="right">Log lines</th><th /></tr></thead>
          <tbody>
            {rows.map((d) => (
              <tr key={d.name}>
                <td className="mono"><strong>{d.name}</strong></td><td className="mono">{d.dataset_id}</td><td><Badge>{kindLabel(d.kind)}</Badge></td>
                <td>{d.title}{d.expect.no_incident && <> <Badge tone="accent">expects “no incident”</Badge></>}{d.expect.uncertainty && <> <Badge tone="accent">expects uncertainty</Badge></>}</td>
                <td>{d.canary ? <><Badge tone="accent">{d.canary}</Badge> <span>{d.canary_name}</span><div className="muted">{d.canary_passes_if}</div></> : "—"}</td>
                <td className="right mono">{d.total_logs.toLocaleString()}</td>
                <td className="right"><button onClick={() => show(d.name)} disabled={!ov.data?.keys_visible} title={ov.data?.keys_visible ? "" : "Start the console with --allow-keys (after blind scoring)"}>Key</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!ov.data?.keys_visible && <p className="muted">Answer keys are hidden so scoring stays blind. Restart with <code>python -m triage_eval serve-ui --allow-keys</code> after scoring.</p>}
      {open && (
        <div className="card">
          <div className="row"><h2 style={{ margin: 0 }}>Answer key · {open}</h2><span className="spacer" /><button onClick={() => setOpen(null)}>Close</button></div>
          {err ? <div className="banner bad">{err}</div> : key ? (
            <>
              <p><strong>Root cause.</strong> {key.root_cause}</p>
              <h3>Key evidence ids</h3>
              <pre>{JSON.stringify(key.tags, null, 1)}</pre>
              <h3>Expectations</h3><pre>{JSON.stringify({ expect: key.expect, canary: key.canary, decoys: key.decoy_patterns }, null, 1)}</pre>
            </>
          ) : <Loading />}
        </div>
      )}
    </Page>
  );
}
