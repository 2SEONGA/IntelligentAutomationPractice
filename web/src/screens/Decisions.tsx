import { useEffect, useState } from "react";
import { Loading, SectionMessage, Typography } from "@montage-ui/core";
import { listDecisions } from "../api";
import { eok, recommendationsDiffer, toneOf } from "../summary";
import type { Decisions as Data, View } from "../types";
import StatusBadge from "../components/StatusBadge";
import LoadError from "../components/LoadError";
import Empty from "../components/Empty";

export default function Decisions({ onNavigate }: { onNavigate: (view: View) => void }) {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const load = () => listDecisions().then((d) => { setData(d); setError(""); }, (e: Error) => setError(e.message));
  useEffect(() => { load(); }, []);
  if (error) return <LoadError title="이력을 불러오지 못했어요" message={error} onRetry={load} />;
  if (!data) return <Loading />;

  return (
    <>
      <div>
        <Typography display="block" variant="title3" weight="bold">의사결정 이력</Typography>
        <Typography display="block" variant="body2" color="semantic.foreground.neutral.secondary">
          사업본부장의 최종 결정과 사유를 모아 둡니다. 다음 유사 공고를 검토할 때 참고하세요 (3주차 To-Be 9단계).
        </Typography>
      </div>
      <div className="kpis" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
        {(["Go", "보류", "No-Go"] as const).map((d) => (
          <div key={d} className="panel stack" style={{ gap: 4 }}>
            <StatusBadge label={`${d} 결정`} tone={d} />
            <Typography variant="title2" weight="bold">{data.counts[d]} <Typography variant="body2" color="semantic.foreground.neutral.secondary">건</Typography></Typography>
          </div>
        ))}
      </div>
      <div className="panel table-scroll" style={{ padding: "4px 16px" }}>
        {data.items.length === 0 ? (
          <Empty>아직 결정된 건이 없어요.</Empty>
        ) : (
          <table className="data-table">
            <thead><tr><th style={{ width: 110 }}>결정일</th><th>공고</th><th style={{ width: 90 }}>예산</th><th style={{ width: 100 }}>AI 권고</th>
              <th style={{ width: 120 }}>체크리스트</th><th style={{ width: 110 }}>결정</th><th>사유</th><th style={{ width: 60 }}>반려</th></tr></thead>
            <tbody>
              {data.items.map((c) => (
                <tr key={c.run_id} className="clickable" onClick={() => onNavigate({ view: "run", runId: c.run_id })}>
                  <td><Typography variant="caption1">{c.decided_at?.slice(0, 16).replace("T", " ")}</Typography></td>
                  <td><button type="button" className="row-link">{c.project_name}</button></td>
                  <td><Typography variant="label2">{c.amount_krw ? eok(c.amount_krw) : "—"}</Typography></td>
                  <td><StatusBadge label={c.ai_recommendation ?? "—"} tone={toneOf(c.ai_recommendation ?? "")} /></td>
                  <td><Typography variant="label2">{c.checklist_recommendation}</Typography></td>
                  <td>
                    <StatusBadge label={c.decision!} tone={c.decision!} />
                    {c.ai_recommendation && recommendationsDiffer(c.ai_recommendation, c.decision!) && (
                      <Typography display="block" variant="caption1" color="semantic.foreground.cautionary.primary">AI 권고와 다름</Typography>
                    )}
                  </td>
                  <td><Typography variant="body2">{c.decision_reason}</Typography></td>
                  <td><Typography variant="label2">{c.rejections}회</Typography></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
