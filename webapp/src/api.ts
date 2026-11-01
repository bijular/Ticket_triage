import { useCallback, useEffect, useState } from "react";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const TOKEN_KEY = "triage_ui_token";
export const getToken = () => sessionStorage.getItem(TOKEN_KEY) ?? "";
export const setToken = (t: string) => sessionStorage.setItem(TOKEN_KEY, t);

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const t = getToken();
  if (t) headers.set("Authorization", `Bearer ${t}`);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const res = await fetch(`/api${path}`, { ...init, headers });
  if (res.status === 401) window.dispatchEvent(new Event("triage-unauthorized"));
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch { /* keep statusText */ }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (path === null) return;
    let live = true;
    setLoading(true);
    api<T>(path)
      .then((d) => live && (setData(d), setError(null)))
      .catch((e) => live && setError(e instanceof ApiError ? e : new ApiError(0, String(e))))
      .finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [path, tick]);
  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, error, loading, reload };
}

// ---- types (only what the UI reads) ----
export interface Overview {
  datasets: number; planned_runs: number; started: number; submitted: number; scored: number; integrity_failures: number;
  by_tool: Record<string, { runs: number; mean: number }>;
  services: { telemetry_api: boolean; ticket_sandbox: boolean; postgres: boolean };
  config_locked: boolean; config_changed_after_lock: boolean; keys_visible: boolean;
  weights: Record<string, number>; tools: string[];
}
export interface Dataset {
  name: string; dataset_id: string; kind: string; title: string | null; canary: string | null; canary_name: string | null;
  canary_passes_if: string | null; benign_lines: number; total_logs: number; alert: string; expect: Record<string, boolean>;
}
export interface PlanRow { run_id: string; tool: string; dataset_id: string; replicate: string; mode: string; name: string | null; status: string }
export interface RunSummary {
  run_id: string; tool: string; dataset_id: string; replicate: number; model?: string; status: string; finished: boolean;
  name?: string; kind?: string; total?: number; top3_hit?: boolean; top1_label?: string; integrity_failures?: string[];
  fabricated?: number; leaked?: number; duplicate_tickets?: number; actions_against_systems?: number; error?: string;
  canary?: { canary: string; grade: string; why: string } | null; cost_usd?: number | null; wall_clock_s?: number | null;
  human_interventions?: number | null; reviewed_by?: number; started?: number;
}
export interface Score {
  name: string; kind: string; total: number; scores: Record<string, number>; suggested: Record<string, number>;
  integrity_failures: string[]; fabricated: string[]; real_citations: number; cited_key_evidence: string[]; leaks: string[];
  leaked: number; top3_hit: boolean; top1_label: string; blamed_decoy_top1: boolean; tickets_created: number; duplicate_tickets: number;
  actions_against_systems: number; injection_expected: boolean; injection_flagged: boolean; injection_followed_heuristic: boolean;
  requests: { total: number; writes: number; retries_after_fault: number; max_identical_repeats: number; pages_followed: number; services_queried: string[] };
  pg?: { statements: number; write_attempts: number };
  canary_grade: { canary: string; grade: string; why: string } | null; error?: string;
  review: Record<string, number[]>;
}
export interface Hypothesis {
  rank: number; title: string; description?: string; confidence?: number; services?: string[];
  evidence?: { ref: string; quote?: string }[];
}
export interface RunDetail {
  manifest: Record<string, unknown>; summary: RunSummary; result: Record<string, any> | null; score: Score | null;
  requests: { ts: number; method: string; path: string; query: string; status: number; write_attempt?: boolean; injected_fault?: number; duplicate?: boolean }[];
  tickets: { event: string; ticket?: Record<string, any>; comment?: Record<string, any>; key?: string }[];
  pg_lines: string[]; has_transcript: boolean; reviews: Record<string, string>[];
}
export interface ReportData {
  report: Record<string, ToolReport>; markdown: string; thresholds: Record<string, unknown>; weights: Record<string, number>; criteria: string[];
}
export interface ToolReport {
  runs: number; mean_score: number; min_score: number; PASS: boolean; integrity_failures: number;
  top3_by_incident: Record<string, { hits: number; runs: number; majority: boolean }>;
  repeatability_agreeing_runs: Record<string, number>; pass_checks: Record<string, boolean>;
  canary_grades: Record<string, Record<string, number>>; silent_misses: Record<string, number>;
  dimension_means: Record<string, number>; cost_per_correct_diagnosis: number | null; total_requests: number; total_retries_after_fault: number;
  holdout_vs_visible_top3_pct: { visible: number | null; holdout: number | null };
}
export interface BlindItem { bid: string; reviews: number; reviewed_by_me: boolean }
export interface BlindDetail {
  bid: string; alert: string; result: Record<string, any>; summary_markdown: string;
  ticket_events: { event: string; ticket?: Record<string, any>; comment?: Record<string, any> }[];
}
