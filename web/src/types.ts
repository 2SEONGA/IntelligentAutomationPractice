import type { CriteriaValues } from "./criteria";
// Mirrors src/bid_review/web/{app,runs,jobs}.py responses.

export type Status = {
  live_available: boolean;
  notice_available: boolean;
  role_note: string;
  checklist_version: string;
  running_job_id: string | null;
};

export type RunSummary = {
  id: string;
  project_name: string;
  created_at: string;
  mode: string;
  via_web: boolean;
  ai_recommendation: string | null;
  decision: string | null;
};

export type ChecklistValue = string | number | null;

export type ChecklistItem = {
  id: string;
  title: string;
  kind: "knockout" | "scoring";
  ai: ChecklistValue;
  value: ChecklistValue;
  overridden: boolean;
  evidence: string;
  source_page: number | null;
};

export type ChecklistSummary = {
  knockout_result: "통과" | "보류" | "No-Go";
  knockout_passed: number;
  score: number;
  score_max: number;
  undetermined: number;
  recommendation: string;
};

export type Checklist = { version: string; criteria_set?: string; items: ChecklistItem[]; summary: ChecklistSummary };

export type Decision = {
  decision: "Go" | "보류" | "No-Go";
  reason: string;
  decided_at: string;
  decision_history: Omit<Decision, "decision_history">[];
};

export type Finding = { topic: string; assessment: string; action?: string | null };

export type Report = {
  decision_status: string;
  executive_summary: string;
  key_findings: Finding[];
  bid_conditions: string[];
  next_actions: string[];
  project_scale: { amount_krw: number | null; source_page: number | null; source_excerpt?: string; rationale?: string } | null;
};

// Pipeline artifacts are large and loosely shaped; tabs read the fields they need.
export type Artifact = Record<string, any> | null;

export type RunDetail = {
  id: string;
  project_name: string;
  created_at: string;
  mode: string;
  via_web: boolean;
  report: Report;
  rfp: Artifact;
  capability: Artifact;
  market: Artifact;
  core_review: Artifact;
  core_review_audit: Artifact;
  details_available: boolean;
  checklist: Checklist;
  decision: Decision | null;
  workflow: Workflow;
  is_current: boolean;
  rerun_available: boolean;
  files: { report_md: string | null; report_json: string; pdf: string | null; notice_pdf?: string | null };
};

export type NodeId = "agent1" | "agent2" | "agent3" | "agent4" | "checklist";
export type StageEvent = { type: "stage"; node: NodeId; state: "start" | "done" | "skip" | "fail" };
export type JobEvent =
  | StageEvent
  | { type: "log"; text: string }
  | { type: "done"; run_id: string }
  | { type: "error"; message: string }
  | { type: "disconnected" };

export type WorkflowStatus = "검토 완료" | "결재 대기" | "보완 요청" | "결정 완료";
export type WorkflowEntry = {
  action: "submit" | "resubmit" | "reject" | "rerun" | "decide";
  from: WorkflowStatus; to: WorkflowStatus; role: "staff" | "director"; at: string; report: string;
  reason?: string; request?: string; decision?: string;
};
export type Workflow = { status: WorkflowStatus; history: WorkflowEntry[] };

export type Case = {
  case_id: string; run_id: string; project_name: string; created_at: string; mode: string; via_web: boolean;
  notice_id: string | null; status: WorkflowStatus; ai_recommendation: string | null; checklist_recommendation: string;
  decision: string | null; decision_reason: string | null; decided_at: string | null; rejections: number;
  last_reject: WorkflowEntry | null; submitted_at: string | null; amount_krw: number | null;
};

export type NoticeVerdict = "검토 대상" | "확인 필요" | "제외";
export type Notice = {
  id: string; bidNtceNm: string; dminsttNm: string; ntceInsttNm: string; cntrctCnclsMthdNm: string; bidClseDt: string;
  bidNtceDtlUrl: string; verdict: NoticeVerdict;
  judged: {
    verdict: NoticeVerdict; reasons: string[]; amount_krw: number | null; deadline: string | null; criteria_set?: string;
    // M33: 40억 선별 기준과 같은 VAT 기준으로 환산한 금액(없으면 예전 판정 → amount_krw 사용)과 원금액의 출처·VAT 기준
    amount_basis_krw?: number | null; amount_vat_basis?: string; amount_source_field?: string | null;
    amount_criterion_basis?: string;
  };
  attachments: { name: string; url: string; kind: "notice" | "rfp" | "pdf" | "other" }[];
  review: { include: boolean; reason: string; role: string; at: string } | null;
  case_folder: string | null; run_id: string | null;
};
export type BatchItem = { id: string; name: string; reason?: string };
export type BatchState = {
  running: boolean; stopped: boolean; total: number; mode: string | null; current: BatchItem | null;
  done: BatchItem[]; waiting: BatchItem[]; failed: BatchItem[];
};
export type CollectRun = { at: string; days: number; keywords: string[]; count: number; skipped_non_ict?: number; pruned?: number };
// 빈 keywords = 키워드 없이 전체 수집(M27)
export const keywordLabel = (keywords: string[]) => (keywords.length ? `키워드 ${keywords.length}개` : "전체 수집");
export type NoticeList = {
  notices: Notice[]; counts: Record<NoticeVerdict | "started", number>; runs: CollectRun[]; notice_available: boolean;
};
export type CollectSummary = { collected: number; total: number; by_verdict: Record<NoticeVerdict, number>; at: string; skipped_non_ict: number; pruned: number };
type StartInfo = { notice_pdf: boolean; skipped: string[]; rfp_guessed: boolean };
export type StartResult = ({ job_id: string } & StartInfo) | ({ needs_upload: true; folder_id: string } & StartInfo);

export type Dashboard = {
  cards: {
    collected_recent: number; collected_total: number; last_run: CollectRun | null; passed: number; awaiting_start: number; confirm: number;
    approvals_pending: number; reviews_total: number; reviews_linked: number;
  };
  funnel: { stage: string; count: number }[];
  distribution: Record<"Go" | "조건부 Go" | "보류" | "No-Go", number>;
  agreement: { matched: number; total: number };
  pending: Case[];
  deadlines: { name: string; agency: string; amount_krw: number | null; amount_basis_krw?: number | null; deadline: string; d_day: number; case_folder: string | null }[];
};
export type Approvals = { pending: Case[]; supplement: Case[]; all: Case[] };
export type Decisions = { counts: Record<"Go" | "보류" | "No-Go", number>; items: Case[] };
export type CriteriaSet = { name: string; default: boolean; active: boolean; updated_at: string | null };
export type Criteria = {
  notice_filter: Record<string, any>; checklist: Record<string, any>;
  sets: CriteriaSet[]; active: string; selected: string; values: CriteriaValues;
  awards_meta: Record<string, any> & { count: number }; role_note: string;
};

export type Menu = "dashboard" | "notices" | "review" | "approvals" | "decisions" | "criteria";
export type View =
  | { view: "dashboard" } | { view: "notices" } | { view: "approvals" } | { view: "decisions" } | { view: "criteria" }
  | { view: "reviews" }
  | { view: "new"; folderId?: string; noticePdf?: boolean; noticeName?: string; notes?: string[] }
  | { view: "job"; jobId: string; notes?: string[] }
  | { view: "run"; runId: string };

export const menuOf = (view: View): Menu =>
  view.view === "new" ? "notices" : view.view === "reviews" || view.view === "job" || view.view === "run" ? "review" : view.view;
