import { useEffect, useState } from "react";
import { Loading, SectionMessage, Typography } from "@montage-ui/core";
import { listApprovals } from "../api";
import type { Role } from "../role";
import { eok, toneOf } from "../summary";
import type { Approvals as Data, Case, View } from "../types";
import StatusBadge from "../components/StatusBadge";
import LoadError from "../components/LoadError";
import FilterBar from "../components/FilterBar";
import { AMOUNT_LABEL, amountBucket, textMatch } from "../filters";
import { RECOMMENDATIONS } from "./ReviewHome";
import Empty from "../components/Empty";

function CaseTable({ cases, empty, extra, onOpen }: {
  cases: Case[]; empty: string; extra?: { head: string; cell: (c: Case) => React.ReactNode }; onOpen: (c: Case) => void;
}) {
  if (cases.length === 0) return <Empty>{empty}</Empty>;
  return (
    <div className="table-scroll"><table className="data-table">
      <thead><tr><th>공고</th><th style={{ width: 90 }}>예산</th><th style={{ width: 100 }}>AI 권고</th><th style={{ width: 130 }}>체크리스트</th>
        {extra && <th>{extra.head}</th>}<th style={{ width: 130 }}>올린 시각</th></tr></thead>
      <tbody>
        {cases.map((c) => (
          <tr key={c.run_id} className="clickable" onClick={() => onOpen(c)}>
            <td><button type="button" className="row-link">{c.project_name}</button>
              {c.notice_id && <Typography display="block" variant="caption1" color="semantic.foreground.neutral.secondary">공고 {c.notice_id}</Typography>}</td>
            <td><Typography variant="label2">{c.amount_krw ? eok(c.amount_krw) : "—"}</Typography></td>
            <td><StatusBadge label={c.ai_recommendation ?? "—"} tone={toneOf(c.ai_recommendation ?? "")} /></td>
            <td><Typography variant="label2">{c.checklist_recommendation}</Typography></td>
            {extra && <td>{extra.cell(c)}</td>}
            <td><Typography variant="caption1" color="semantic.foreground.neutral.secondary">{c.submitted_at?.slice(5, 16).replace("T", " ") ?? "—"}</Typography></td>
          </tr>
        ))}
      </tbody>
    </table></div>
  );
}

export default function Approvals({ role, onNavigate }: { role: Role; onNavigate: (view: View) => void }) {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [rec, setRec] = useState("all");
  const [budget, setBudget] = useState("all");
  const load = () => listApprovals().then((d) => { setData(d); setError(""); }, (e: Error) => setError(e.message));
  useEffect(() => { load(); }, []);
  if (error) return <LoadError title="결재함을 불러오지 못했어요" message={error} onRetry={load} />;
  if (!data) return <Loading />;
  const open = (c: Case) => onNavigate({ view: "run", runId: c.run_id });
  const pick = (cases: Case[]) => cases.filter((c) => textMatch(query, c.project_name, c.notice_id)
    && (rec === "all" || c.ai_recommendation === rec) && (budget === "all" || amountBucket(c.amount_krw) === budget));
  const pending = pick(data.pending);
  const supplement = pick(data.supplement);
  const total = role === "director" ? data.pending.length : data.pending.length + data.supplement.length;
  const rejection = {
    head: "반려 사유",
    cell: (c: Case) => (
      <Typography variant="body2">{c.last_reject?.request ? `[${c.last_reject.request}] ` : ""}{c.last_reject?.reason ?? "—"}</Typography>
    ),
  };

  return (
    <>
      <div>
        <Typography display="block" variant="title3" weight="bold">결재함</Typography>
        <Typography display="block" variant="body2" color="semantic.foreground.neutral.secondary">
          {role === "director" ? "담당자가 올린 검토 보고서를 결정하거나 반려합니다 (HITL-1)." : "반려된 건을 보완하고, 올린 결재의 진행 상황을 봅니다."}
        </Typography>
      </div>
      <FilterBar query={query} onQuery={setQuery} placeholder="공고명·공고번호 검색"
        shown={role === "director" ? pending.length : pending.length + supplement.length} total={total}
        groups={[
          { label: "AI 권고", value: rec, onChange: setRec, options: RECOMMENDATIONS.map((r) => ({ value: r, label: r })) },
          { label: "예산", value: budget, onChange: setBudget, options: Object.entries(AMOUNT_LABEL).map(([value, label]) => ({ value, label })) },
        ]} />
      {role === "director" ? (
        <div className="panel stack" style={{ gap: 8 }}>
          <Typography variant="headline2" weight="bold">결재 대기 {data.pending.length}</Typography>
          <CaseTable cases={pending} empty={data.pending.length ? "검색·필터에 맞는 건이 없어요." : "결재 대기 중인 건이 없어요."} onOpen={open} />
        </div>
      ) : (
        <>
          <div className="panel stack" style={{ gap: 8 }}>
            <Typography variant="headline2" weight="bold">보완 요청 {data.supplement.length}</Typography>
            <CaseTable cases={supplement} empty={data.supplement.length ? "검색·필터에 맞는 건이 없어요." : "반려된 건이 없어요."} extra={rejection} onOpen={open} />
          </div>
          <div className="panel stack" style={{ gap: 8 }}>
            <Typography variant="headline2" weight="bold">올린 결재 대기 {data.pending.length}</Typography>
            <CaseTable cases={pending} empty={data.pending.length ? "검색·필터에 맞는 건이 없어요." : "결재를 올린 건이 없어요."} onOpen={open} />
          </div>
        </>
      )}
    </>
  );
}
