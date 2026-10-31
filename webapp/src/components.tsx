import { ReactNode, useState } from "react";
import { ApiError } from "./api";

export const Badge = ({ children, tone = "" }: { children: ReactNode; tone?: "good" | "bad" | "warn" | "accent" | "" }) => (
  <span className={`badge ${tone}`}>{children}</span>
);

export const Stat = ({ n, l, tone }: { n: ReactNode; l: string; tone?: string }) => (
  <div className="card stat"><div className="n" style={tone ? { color: `var(--${tone})` } : undefined}>{n}</div><div className="l">{l}</div></div>
);

export const Bar = ({ value, max = 100, tone = "" }: { value: number; max?: number; tone?: string }) => (
  <div className={`bar ${tone}`} role="progressbar" aria-valuenow={value} aria-valuemax={max}>
    <i style={{ width: `${Math.max(0, Math.min(100, (100 * value) / (max || 1)))}%` }} />
  </div>
);

export const Loading = () => <div className="empty">Loading…</div>;

export function ErrorBox({ error }: { error: ApiError }) {
  return <div className="banner bad" role="alert"><strong>{error.status ? `Error ${error.status}` : "Error"}:</strong> {error.message}</div>;
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: [T, string][]; value: T; onChange: (t: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map(([k, label]) => (
        <button key={k} role="tab" aria-selected={value === k} className={value === k ? "on" : ""} onClick={() => onChange(k)}>{label}</button>
      ))}
    </div>
  );
}

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <button onClick={() => navigator.clipboard.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1500); })}>
      {done ? "Copied ✓" : label}
    </button>
  );
}

export const StatusBadge = ({ status }: { status: string }) => (
  <Badge tone={status === "submitted" ? "good" : status === "started" ? "warn" : ""}>{status}</Badge>
);

export const GradeBadge = ({ grade }: { grade?: string }) =>
  grade ? <Badge tone={grade === "Caught" ? "good" : grade === "Silent_miss" ? "bad" : grade === "Manual" ? "accent" : "warn"}>{grade.replace("_", " ")}</Badge> : <>—</>;

export const ScoreBadge = ({ total, integrity }: { total?: number; integrity?: boolean }) =>
  total == null ? <>—</> : <Badge tone={integrity ? "bad" : total >= 80 ? "good" : total >= 50 ? "warn" : "bad"}>{total.toFixed(1)}</Badge>;

export const Check = ({ ok }: { ok: boolean }) => <span className={`check ${ok ? "ok" : "no"}`} aria-label={ok ? "pass" : "fail"}>{ok ? "✓" : "✗"}</span>;

export function Page({ title, sub, actions, children }: { title: string; sub?: string; actions?: ReactNode; children: ReactNode }) {
  return (
    <>
      <div className="row" style={{ alignItems: "flex-start" }}>
        <div><h1>{title}</h1>{sub && <p className="sub">{sub}</p>}</div>
        <span className="spacer" />{actions}
      </div>
      {children}
    </>
  );
}
