import { useEffect, useState } from "react";
import { Loading, Typography } from "@montage-ui/core";
import { listApprovals, listRuns } from "../api";
import type { Role } from "../role";
import type { Case, RunSummary, View } from "../types";
import StatusBadge from "../components/StatusBadge";
import Empty from "../components/Empty";
import LoadError from "../components/LoadError";
import FilterBar from "../components/FilterBar";
import { textMatch } from "../filters";

const MODE_LABEL: Record<string, string> = { offline: "offline", live: "live" };
export const STATUS_TONE: Record<string, string> = { "검토 완료": "none", "결재 대기": "", "보완 요청": "No-Go", "결정 완료": "Go" };

export function runBadge(run: { decision: string | null; ai_recommendation: string | null }): { label: string; tone: string } {
  if (run.decision) return { label: `${run.decision} 결정`, tone: run.decision };
  return { label: `AI: ${run.ai_recommendation ?? "미확인"}`, tone: run.ai_recommendation ?? "" };
}

type Props = { role: Role; runningJobId: string | null; onNavigate: (view: View) => void };

export const RECOMMENDATIONS = ["Go", "조건부 Go", "보류", "No-Go"];
const STATES = ["검토 완료", "결재 대기", "보완 요청", "결정 완료", "이전 보고서"];

/** 검토 menu home: every report (v1 list), with the case's approval status on its current report. */
export default function ReviewHome({ role, runningJobId, onNavigate }: Props) {
  const [rows, setRows] = useState<{ run: RunSummary; case?: Case }[] | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [rec, setRec] = useState("all");
  const [state, setState] = useState("all");

  const load = () => Promise.all([listRuns(), listApprovals()]).then(([runs, approvals]) => {
    const byRun = new Map(approvals.all.map((c) => [c.run_id, c]));
    setRows(runs.map((run) => ({ run, case: byRun.get(run.id) })));
    setError("");
  }, (e: Error) => setError(e.message));
  useEffect(() => { load(); }, []);
  const shown = rows?.filter(({ run, case: c }) => textMatch(query, run.project_name, c?.notice_id)
    && (rec === "all" || run.ai_recommendation === rec)
    && (state === "all" || (c?.status ?? "이전 보고서") === state));

  return (
    <>
      <div className="row">
        <div className="grow">
          <Typography display="block" variant="title3" weight="bold">검토</Typography>
          <Typography display="block" variant="body2" color="semantic.foreground.neutral.secondary">
            Agent 검토 결과와 결재 상태입니다. 새 검토는 공고함에서 시작해요(수집한 공고 또는 PDF로 새 검토).
          </Typography>
        </div>
      </div>
      {runningJobId && (
        <button className="panel run-row" onClick={() => onNavigate({ view: "job", jobId: runningJobId })}>
          <StatusBadge label="분석 진행 중" />
          <Typography variant="label1" weight="bold">진행 화면 보기 →</Typography>
        </button>
      )}
      {rows && rows.length > 0 && (
        <FilterBar query={query} onQuery={setQuery} placeholder="공고명·공고번호 검색" shown={shown?.length} total={rows.length}
          groups={[
            { label: "AI 권고", value: rec, onChange: setRec, options: RECOMMENDATIONS.map((r) => ({ value: r, label: r })) },
            { label: "결재 상태", value: state, onChange: setState, options: STATES.map((r) => ({ value: r, label: r })) },
          ]} />
      )}
      {error ? <LoadError title="검토 목록을 불러오지 못했어요" message={error} onRetry={load} /> : !rows || !shown ? <Loading /> : shown.length === 0 ? (
        <div className="panel"><Empty>{rows.length ? "검색·필터에 맞는 검토가 없어요." : "아직 검토한 공고가 없어요."}</Empty></div>
      ) : (
        <div className="panel" style={{ padding: "4px 16px" }}>
          <div className="table-scroll"><table className="data-table">
            <thead><tr><th>공고</th><th style={{ width: 190 }}>실행</th><th style={{ width: 120 }}>권고·결정</th><th style={{ width: 110 }}>결재 상태</th></tr></thead>
            <tbody>
              {shown.map(({ run, case: c }) => {
                const badge = runBadge(run);
                return (
                  <tr key={run.id} className="clickable" onClick={() => onNavigate({ view: "run", runId: run.id })}>
                    <td><button type="button" className="row-link">{run.project_name}</button>
                      {c?.notice_id && <Typography display="block" variant="caption1" color="semantic.foreground.neutral.secondary">공고 {c.notice_id}</Typography>}</td>
                    <td><Typography variant="caption1" color="semantic.foreground.neutral.secondary">
                      {run.created_at.slice(5, 10).replace("-", "/")} {run.created_at.slice(11, 16)} · {MODE_LABEL[run.mode] ?? run.mode}{run.via_web ? " · 웹" : ""}
                    </Typography></td>
                    <td><StatusBadge label={badge.label} tone={badge.tone} /></td>
                    <td>{c ? <StatusBadge label={c.status} tone={STATUS_TONE[c.status]} />
                      : <Typography variant="caption1" color="semantic.foreground.neutral.tertiary">이전 보고서</Typography>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table></div>
        </div>
      )}
    </>
  );
}
