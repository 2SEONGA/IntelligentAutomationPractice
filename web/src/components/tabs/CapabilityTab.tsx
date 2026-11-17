import { Typography } from "@montage-ui/core";
import StatusBadge from "../StatusBadge";

const TONE: Record<string, string> = { 충족: "Go", "부분 충족": "보류", 미충족: "No-Go", "확인 필요": "none" };

export default function CapabilityTab({ capability }: { capability: Record<string, any> }) {
  const matches: any[] = [...(capability.eligibility_matches ?? []), ...(capability.staffing_matches ?? []), ...(capability.requirement_matches ?? [])];
  const fit = capability.fit_score ?? {};
  const review: string[] = capability.human_review_items ?? [];
  return (
    <div className="stack">
      <div className="panel stack" style={{ gap: 8 }}>
        <Typography display="block" variant="headline2" weight="bold">요구사항 적합도 {fit.value ?? "-"}%</Typography>
        <Typography display="block" variant="caption1" color="semantic.foreground.neutral.secondary">{fit.formula}</Typography>
        {capability.company_evidence_notice ? (
          <Typography display="block" variant="body2" color="semantic.foreground.cautionary.primary">{capability.company_evidence_notice}</Typography>
        ) : null}
      </div>
      <div className="panel stack" style={{ gap: 12 }}>
        <Typography display="block" variant="headline2" weight="bold">항목별 매칭 {matches.length}건</Typography>
        <table className="data-table">
          <thead><tr><th style={{ width: 110 }}>ID</th><th style={{ width: 100 }}>상태</th><th>확인할 내용</th></tr></thead>
          <tbody>
            {matches.map((match, i) => (
              <tr key={`${i}-${match.requirement_id}`}>
                <td><Typography variant="label2" weight="bold">{match.requirement_id}</Typography></td>
                <td><StatusBadge label={match.status} tone={TONE[match.status] ?? "none"} /></td>
                <td>
                  <Typography variant="body2">{match.gap_or_check_item || match.score_rationale}</Typography>
                  {(match.retrieved_candidates ?? []).length > 0 ? (
                    <div className="stack" style={{ gap: 6, marginTop: 10 }}>
                      <Typography variant="label2" weight="bold">RAG 검색 근거 후보</Typography>
                      <Typography variant="caption1" color="semantic.foreground.neutral.secondary">
                        키워드·의미 검색 후보입니다. 적합도 점수와 자격 판정에는 자동 반영되지 않습니다.
                      </Typography>
                      {(match.retrieved_candidates as any[]).map((candidate, j) => (
                        <div key={`${candidate.record_id}-${j}`} className="panel stack" style={{ gap: 4 }}>
                          <Typography variant="label2" weight="bold">{candidate.title}</Typography>
                          <Typography variant="caption1">{candidate.excerpt}</Typography>
                          <Typography variant="caption1" color="semantic.foreground.neutral.secondary">
                            {candidate.source_title}{candidate.source_section ? ` · ${candidate.source_section}` : ""}
                            {candidate.evidence_level ? ` · ${candidate.evidence_level}` : ""}
                            {` · ${candidate.retrieval_method ?? "키워드 검색"}`}
                            {typeof candidate.semantic_score === "number" ? ` (유사도 ${candidate.semantic_score.toFixed(2)})` : ""}
                          </Typography>
                          {candidate.data_freshness === "latest_filing_pending_primary_source_review" ? (
                            <Typography variant="caption1" color="semantic.foreground.cautionary.primary">
                              최신 공시 원문 확인 대기
                            </Typography>
                          ) : null}
                          {candidate.verification_required ? (
                            <Typography variant="caption1">확인 필요: {candidate.verification_required}</Typography>
                          ) : null}
                          {typeof candidate.source_url === "string" && candidate.source_url.startsWith("https://") ? (
                            <a href={candidate.source_url} target="_blank" rel="noopener noreferrer">출처 원문 열기</a>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {review.length > 0 ? (
        <div className="panel stack" style={{ gap: 8 }}>
          <Typography display="block" variant="headline2" weight="bold">담당자 확인 항목 {review.length}건</Typography>
          <ul className="aligned-list">
            {review.map((item, i) => <li key={i}><Typography variant="body2">{item}</Typography></li>)}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
