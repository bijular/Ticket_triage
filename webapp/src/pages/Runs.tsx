import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { RunSummary, useApi } from "../api";
import { Badge, ErrorBox, GradeBadge, Loading, Page, ScoreBadge, StatusBadge } from "../components";
import { fmtUsd, kindLabel } from "../format";

type SortKey = "run_id" | "tool" | "name" | "total" | "status";

export default function Runs() {
  const runs = useApi<RunSummary[]>("/runs");
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [tool, setTool] = useState("");
  const [onlyBad, setOnlyBad] = useState(false);
  const [sort, setSort] = useState<[SortKey, 1 | -1]>(["run_id", 1]);
  const rows = useMemo(() => {
    const r = (runs.data ?? []).filter((x) => (!tool || x.tool === tool) && (!q || x.run_id.includes(q) || (x.name ?? "").includes(q)) &&
      (!onlyBad || (x.integrity_failures?.length ?? 0) > 0));
    const [k, dir] = sort;
    return [...r].sort((a, b) => ((a[k] ?? "") > (b[k] ?? "") ? dir : -dir));
  }, [runs.data, q, tool, onlyBad, sort]);
  if (runs.error) return <ErrorBox error={runs.error} />;
  if (!runs.data) return <Loading />;
  const th = (k: SortKey, label: string) => (
    <th aria-sort={sort[0] === k ? (sort[1] === 1 ? "ascending" : "descending") : "none"}>
      <button style={{ all: "unset", cursor: "pointer" }} onClick={() => setSort([k, sort[0] === k ? (-sort[1] as 1 | -1) : 1])}>{label}{sort[0] === k ? (sort[1] === 1 ? " ▲" : " ▼") : ""}</button>
    </th>
  );
  return (
    <Page title="Runs" sub="Every started run with its automatic score. Integrity failures score 0." actions={<button onClick={runs.reload}>Refresh</button>}>
      <div className="filters">
        <input placeholder="Search run or dataset" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search" />
        <select aria-label="Tool" value={tool} onChange={(e) => setTool(e.target.value)}><option value="">All tools</option><option>factory</option><option>cognition</option><option>cursor</option></select>
        <label className="row"><input type="checkbox" checked={onlyBad} onChange={(e) => setOnlyBad(e.target.checked)} /> Integrity failures only</label>
      </div>
      {!rows.length ? <div className="card empty">No runs yet. <a href="/new">Start one</a>.</div> : (
        <div className="card" style={{ padding: 0, overflow: "auto" }}>
          <table>
            <thead><tr>{th("run_id", "Run")}{th("tool", "Tool")}{th("name", "Dataset")}<th>Kind</th>{th("status", "Status")}{th("total", "Score")}<th>Top-3</th><th>Canary</th><th>Flags</th><th>Cost</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.run_id} className="click" onClick={() => nav(`/runs/${r.run_id}`)}>
                  <td className="mono">{r.run_id}</td><td style={{ textTransform: "capitalize" }}>{r.tool}</td><td className="mono">{r.name}</td>
                  <td><Badge>{kindLabel(r.kind)}</Badge></td><td><StatusBadge status={r.status} /></td>
                  <td>{r.error ? <Badge tone="bad">error</Badge> : <ScoreBadge total={r.total} integrity={!!r.integrity_failures?.length} />}</td>
                  <td>{r.top3_hit == null ? "—" : r.top3_hit ? <Badge tone="good">hit</Badge> : <Badge tone="bad">miss</Badge>}</td>
                  <td>{r.canary ? <><span className="muted">{r.canary.canary} </span><GradeBadge grade={r.canary.grade} /></> : "—"}</td>
                  <td>
                    {!!r.fabricated && <Badge tone="bad">fabricated {r.fabricated}</Badge>} {!!r.leaked && <Badge tone="bad">leak</Badge>}{" "}
                    {!!r.duplicate_tickets && <Badge tone="warn">dup ticket</Badge>} {!!r.actions_against_systems && <Badge tone="bad">action</Badge>}
                  </td>
                  <td>{fmtUsd(r.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Page>
  );
}
