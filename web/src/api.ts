import type {
  Approvals, Artifact, BatchState, Checklist, ChecklistValue, CollectSummary, Criteria, Dashboard, Decision, Decisions, JobEvent,
  NoticeList, RunDetail, RunSummary, StartResult, Status, Workflow,
} from "./types";
import type { Role } from "./role";
import type { CriteriaValues } from "./criteria";

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly state?: string) {
    super(message);
  }
}

let currentRole: Role = "staff";
/** Every request carries the demo role; the server checks it against spec 4.1. */
export const setApiRole = (role: Role) => {
  currentRole = role;
};

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-Role", currentRole);
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(body?.detail ?? `요청 실패 (${response.status})`, response.status, body?.status);
  }
  return response.json();
}

const send = (method: "POST" | "PUT", body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const getStatus = () => call<Status>("/api/status");
export const listRuns = () => call<RunSummary[]>("/api/runs");
export const getRun = (id: string) => call<RunDetail>(`/api/runs/${id}`);
export const updateCoreReview = (id: string, action: "analyze" | "approve_and_run_t2" | "reject", reason = "") =>
  call<{ result: Artifact; audit: Artifact }>(`/api/runs/${id}/core-review`, send("POST", { action, reason }));
export const startRun = (form: FormData) => call<{ job_id: string }>("/api/runs", { method: "POST", body: form });
export const saveChecklist = (id: string, overrides: Record<string, ChecklistValue>) =>
  call<Checklist>(`/api/runs/${id}/checklist`, send("PUT", { overrides }));
export const saveDecision = (id: string, decision: string, reason: string) =>
  call<Decision>(`/api/runs/${id}/decision`, send("PUT", { decision, reason }));
export const fileUrl = (id: string, name: string) => `/api/runs/${id}/files/${name}`;

export const getDashboard = () => call<Dashboard>("/api/dashboard");
export const listNotices = () => call<NoticeList>("/api/notices");
export const collectNotices = (days: number, keywords?: string[]) =>
  call<CollectSummary>("/api/notices/collect", send("POST", { days, keywords }));
export const reviewNotice = (id: string, include: boolean, reason: string) =>
  call<unknown>(`/api/notices/${id}/review`, send("PUT", { include, reason }));
export const startNotice = (id: string, mode: string, marketApi = false) =>
  call<StartResult>(`/api/notices/${id}/start`, send("POST", { mode, market_api: marketApi }));
export const transition = (id: string, action: "submit" | "resubmit" | "reject", reason = "", request = "") =>
  call<Workflow>(`/api/runs/${id}/workflow`, send("PUT", { action, reason, request }));
export const rerun = (id: string, mode: string, marketApi = false) =>
  call<{ job_id: string }>(`/api/runs/${id}/rerun`, send("POST", { mode, market_api: marketApi }));
export const startAll = (mode: string) => call<{ total: number }>("/api/notices/start-all", send("POST", { mode }));
export const getBatch = () => call<BatchState>("/api/notices/batch");
export const stopBatch = () => call<BatchState>("/api/notices/batch/stop", { method: "POST" });
export const listApprovals = () => call<Approvals>("/api/approvals");
export const listDecisions = () => call<Decisions>("/api/decisions");
export const getCriteria = (set?: string) =>
  call<Criteria>(set ? `/api/criteria?set=${encodeURIComponent(set)}` : "/api/criteria");
export const saveCriteriaSet = (name: string, values: CriteriaValues) =>
  call<{ name: string }>(`/api/criteria/sets/${encodeURIComponent(name.trim())}`, send("PUT", { values }));
export const deleteCriteriaSet = (name: string) =>
  call<{ ok: boolean }>(`/api/criteria/sets/${encodeURIComponent(name)}`, { method: "DELETE" });
export const applyCriteriaSet = (name: string) => call<{ active: string }>("/api/criteria/active", send("PUT", { name }));

/** Subscribes to a job's SSE stream; returns an unsubscribe function. */
export function jobEvents(jobId: string, onEvent: (event: JobEvent) => void): () => void {
  const source = new EventSource(`/api/jobs/${jobId}/events`);
  source.onmessage = (message) => {
    const event = JSON.parse(message.data) as JobEvent;
    onEvent(event);
    if (event.type === "done" || event.type === "error") source.close();
  };
  // EventSource's own retry would replay the log into the same screen; the caller resubscribes from a clean state.
  source.onerror = () => {
    source.close();
    onEvent({ type: "disconnected" });
  };
  return () => source.close();
}
