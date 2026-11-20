import { useState } from "react";
import { Button, SectionMessage, TextArea, Typography } from "@montage-ui/core";
import { updateCoreReview } from "../../api";
import type { Role } from "../../role";
import type { RunDetail } from "../../types";
import PageLink from "../PageLink";
import CapabilityTab from "./CapabilityTab";
import StatusBadge from "../StatusBadge";

type Props = { run: RunDetail; role: Role; onChanged: () => void; onOpenRun?: (runId: string) => void };
const LABELS: Record<string, string> = { amount: "금액", eligibility: "참가자격", schedule: "기간·일정" };
const STATUS: Record<string, string> = {
  confirmed: "확인 완료", retrieve_more: "추가 검색 필요", conflict: "충돌", human_review: "담당자 확인 필요",
};

// 공고문 인용: 실행 폴더 notice.pdf에서 추출했거나 RAG 색인의 공고문 PDF chunk
const isNotice = (citation: any) => citation.source_path === "notice.pdf" || citation.document_type === "notice_pdf";

export default function CoreReviewTab({ run, role, onChanged, onOpenRun }: Props) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const review = run.core_review as Record<string, any> | null;
  const audit = run.core_review_audit as Record<string, any> | null;
  const verification = review?.verification as Record<string, any> | undefined;
  const awaitingReview = review?.decision === "human_review_required";
  const canEdit = role === "staff" && run.is_current && ["검토 완료", "보완 요청"].includes(run.workflow.status);

  const act = async (action: "analyze" | "approve_and_run_t2" | "reject") => {
    setBusy(true);
    setError("");
    try {
      const saved = await updateCoreReview(run.id, action, reason);
      setReason("");
      // 검증·승인은 새 보고서를 만든다(승인 → T4 보고서). 그 보고서로 이동한다.
      if (saved.run_id && saved.run_id !== run.id && onOpenRun) onOpenRun(saved.run_id);
      else onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <div className="panel stack" style={{ gap: 12 }}>
        <div className="row">
          <Typography className="grow" variant="headline2" weight="bold">T1.5 핵심정보 검증</Typography>
          {verification && <StatusBadge label={STATUS[verification.status] ?? verification.status} tone={verification.status === "confirmed" ? "Go" : "보류"} />}
          {canEdit && (!review || review.decision === "human_rejected") && <Button size="small" loading={busy} onClick={() => act("analyze")}>{review ? "다시 검증" : "검증 실행"}</Button>}
        </div>
        <Typography variant="body2" color="semantic.foreground.neutral.secondary">
          금액·참가자격·기간 정보를 원문 근거와 대조합니다. 승인하면 아래 기록된 미확인 사항을 수용하고 현재 T1 추출값으로 T2 회사 역량 분석을 실행합니다.
        </Typography>
        {error && <SectionMessage variant="negative">{error}</SectionMessage>}
        {!review && <SectionMessage variant="info">아직 T1.5 검증 결과가 없습니다. 검토 실행을 눌러 분석을 시작하세요.</SectionMessage>}
        {review?.decision === "human_approved_company_matching_complete" && (
          <SectionMessage variant="cautionary">
            담당자가 미확인 항목을 확인하고 T2 진행을 승인했습니다. T1.5 상태는 원래 판정대로 보존됩니다.
            {review.human_override?.note ? ` 승인 사유: ${review.human_override.note}` : ""}
          </SectionMessage>
        )}
        {review?.decision === "human_rejected" && (
          <SectionMessage variant="negative">담당자가 T2 진행을 반려했습니다. 사유: {review.review_note}</SectionMessage>
        )}
        {verification ? (
          <SectionMessage variant={verification.g2b_lookup_status === "found" ? "info" : "cautionary"}>
            나라장터 API 상태: {verification.g2b_lookup_status ?? "미조회"}
            {verification.g2b_lookup_error ? ` · ${verification.g2b_lookup_error}` : ""}
          </SectionMessage>
        ) : null}
        {verification ? (
          <SectionMessage variant={verification.notice_pdf_status === "analyzed" ? "info" : "cautionary"}>
            공고문 PDF 추출 상태: {verification.notice_pdf_status === "analyzed" ? "페이지 추출 완료 · T1 일정 후보는 담당자 확인 필요" : verification.notice_pdf_status === "unavailable" ? "파일을 찾지 못해 추출하지 못함" : "공고문 PDF 미제공"}
          </SectionMessage>
        ) : null}
        {verification ? (
          <SectionMessage variant={verification.g2b_attachment_lookup_status === "found" ? "info" : "cautionary"}>
            G2B 첨부 메타데이터 조회: {verification.g2b_attachment_lookup_status ?? "미조회"}
            {verification.g2b_attachment_lookup_error ? ` · ${verification.g2b_attachment_lookup_error}` : " · 공고번호·차수 기준 첨부파일정보 오퍼레이션"}
          </SectionMessage>
        ) : null}
        {verification?.g2b_attachment_comparison ? (
          <SectionMessage variant={verification.g2b_attachment_comparison.status === "byte_match" ? "info" : "cautionary"}>
            G2B 첨부 파일 비교: {verification.g2b_attachment_comparison.status === "byte_match" ? "현재 API 첨부와 저장된 공고문 PDF의 바이트가 일치합니다." : verification.g2b_attachment_comparison.status === "byte_mismatch" ? "API 첨부와 저장된 PDF가 다릅니다. 정정 이력과 문서 내용을 담당자가 대조해야 합니다." : `자동 비교 상태 ${verification.g2b_attachment_comparison.status ?? "미확인"}`}
            {verification.g2b_attachment_comparison.local_sha256 ? ` · 로컬 SHA-256 ${verification.g2b_attachment_comparison.local_sha256}` : ""}
            {verification.g2b_attachment_comparison.g2b_sha256 ? ` · API 첨부 SHA-256 ${verification.g2b_attachment_comparison.g2b_sha256}` : ""}
          </SectionMessage>
        ) : null}
        {verification?.g2b_change_history?.status ? (
          <SectionMessage variant={verification.g2b_change_history.status === "no_changes" ? "info" : "cautionary"}>
            나라장터 변경이력: {verification.g2b_change_history.status === "no_changes" ? "변경(정정) 이력 없음" : verification.g2b_change_history.status === "changes_found" ? `${verification.g2b_change_history.changes.length}건 — 정정 후 값과 인용 판본을 대조하세요` : `조회 실패${verification.g2b_change_history.error ? ` · ${verification.g2b_change_history.error}` : ""}`}
            {((verification.g2b_change_history.changes ?? []) as any[]).map((row, index) => (
              <div key={index}>· {row.chgDt} 차수 {row.bidNtceOrd} {row.chgItemNm}: {row.bfchgVal ?? "-"} → {row.afchgVal ?? "-"}</div>
            ))}
          </SectionMessage>
        ) : null}
      </div>

      {(verification?.linked_attachments ?? []).length > 0 ? (
        <div className="panel stack" style={{ gap: 8 }}>
          <Typography variant="headline2" weight="bold">나라장터 공고 첨부</Typography>
          <Typography variant="caption1" color="semantic.foreground.neutral.secondary">
            {verification?.g2b_attachment_lookup_status === "found" ? "공고번호·차수 기준 첨부파일정보 오퍼레이션의 URL입니다." : "목록 응답 fallback 첨부 URL입니다. 별도 첨부정보 조회는 완료되지 않았습니다."} 파일 본문을 확인하기 전까지 판본 확인 완료로 보지 않습니다.
          </Typography>
          {((verification?.linked_attachments ?? []) as any[]).map((attachment, index) => (
            <div key={`${attachment.name}-${index}`}>
              <Typography variant="body2">{attachment.name} · {attachment.document_kind ?? attachment.kind} · {attachment.metadata_source ?? "목록 응답"}</Typography>
              {typeof attachment.url === "string" && attachment.url.startsWith("https://") ? (
                <a href={attachment.url} target="_blank" rel="noopener noreferrer">첨부 원문 열기</a>
              ) : <Typography variant="caption1">유효한 HTTPS 링크가 없습니다.</Typography>}
            </div>
          ))}
        </div>
      ) : null}

      {review?.capability && (
        <div className="stack">
          <Typography display="block" variant="headline2" weight="bold">T2 회사 역량 분석</Typography>
          <CapabilityTab capability={review.capability} />
          {review.similar_projects_status ? (
            <div className="panel stack" style={{ gap: 8 }}>
              <Typography display="block" variant="headline2" weight="bold">과거 유사 사업 (Agent 3)</Typography>
              <Typography display="block" variant="caption1" color="semantic.foreground.neutral.secondary">
                최근 나라장터 ICT 용역 RFP와 사업 프로필(개요·목적·범위) 유사도를 비교한 후보입니다. 같은 사업이나 유사 실적 인정을 뜻하지 않으며, 낙찰 정보는 공고번호가 일치할 때만 표시합니다. 상태: {review.similar_projects_status}
              </Typography>
              {((review.similar_projects ?? []) as any[]).map((project) => (
                <div key={project.notice_id} className="stack" style={{ gap: 2 }}>
                  <Typography variant="label2" weight="bold">
                    {project.title} · {project.buyer} · 유사도 {Number(project.similarity).toFixed(2)}{project.same_project_suspected ? " · 동일 사업 재공고 의심" : ""}
                  </Typography>
                  <Typography variant="caption1">
                    {project.award ? `낙찰: ${project.award.bidwinnrNm} ${Number(project.award.sucsfbidAmt).toLocaleString("ko-KR")}원` : "낙찰 정보 없음(공고번호 일치 결과 없음)"}
                    {` · 공고 ${(project.related_notice_ids ?? [project.notice_id]).join(", ")}`}
                  </Typography>
                  {typeof project.notice_url === "string" && project.notice_url.startsWith("https://") ? (
                    <a href={project.notice_url} target="_blank" rel="noopener noreferrer">나라장터 공고 열기</a>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      )}

      {verification && Object.entries(verification.categories ?? {}).map(([key, category]: [string, any]) => (
        <div className="panel stack" style={{ gap: 10 }} key={key}>
          <div className="row">
            <Typography className="grow" variant="headline2" weight="bold">{LABELS[key] ?? key}</Typography>
            <StatusBadge label={STATUS[category.status] ?? category.status} tone={category.status === "confirmed" ? "Go" : "보류"} />
          </div>
          <Typography variant="caption1" color="semantic.foreground.neutral.secondary">{category.coverage_note}</Typography>
          {(category.claims ?? []).map((claim: any, index: number) => (
            <details key={`${claim.field}-${index}`} className="panel" style={{ padding: 12 }}>
              <summary style={{ cursor: "pointer" }}>
                <b>{claim.field}</b> · {STATUS[claim.status] ?? claim.status}
                {claim.claim != null && ` · ${typeof claim.claim === "object" ? JSON.stringify(claim.claim) : String(claim.claim)}`}
              </summary>
              <div className="stack" style={{ gap: 8, marginTop: 12 }}>
                {(claim.checks ?? []).map((check: any) => (
                  <div key={check.check_id}>
                    <Typography variant="label2" weight="bold">{check.label} · {STATUS[check.status] ?? check.status}</Typography>
                    <Typography display="block" variant="body2">{check.detail}</Typography>
                  </div>
                ))}
                {(claim.citations ?? []).map((citation: any) => (
                  <details key={citation.chunk_id}>
                    <summary style={{ cursor: "pointer" }}>
                      원문 근거 · {citation.document_title} {citation.page ? `${citation.page}쪽` : citation.field_name ?? ""}
                      {citation.section && ` · ${citation.section}`}
                      {citation.page && <PageLink runId={run.id} page={citation.page}
                        pdfAvailable={isNotice(citation) ? !!run.files.notice_pdf : !!run.files.pdf}
                        fileName={isNotice(citation) ? "notice.pdf" : "rfp.pdf"} />}
                    </summary>
                    <blockquote style={{ whiteSpace: "pre-wrap", margin: "8px 0", fontSize: 13 }}>{citation.quote}</blockquote>
                    {typeof citation.source_url === "string" && citation.source_url.startsWith("https://") ? (
                      <a href={citation.source_url} target="_blank" rel="noopener noreferrer">공고별 상세 링크 열기</a>
                    ) : null}
                    {typeof citation.api_source_url === "string" && citation.api_source_url.startsWith("https://") ? (
                      <div><a href={citation.api_source_url} target="_blank" rel="noopener noreferrer">API 출처 설명</a></div>
                    ) : null}
                  </details>
                ))}
                {(claim.unresolved_questions ?? []).map((question: string, i: number) => (
                  <Typography key={i} variant="caption1" color="semantic.foreground.cautionary.primary">확인 필요: {question}</Typography>
                ))}
              </div>
            </details>
          ))}
          {(category.scope_exclusions ?? []).length > 0 && (
            <details className="panel" style={{ padding: 12 }}>
              <summary style={{ cursor: "pointer" }}>
                참가자격 외 항목으로 분류한 후보 {category.scope_exclusions.length}건
              </summary>
              <div className="stack" style={{ gap: 8, marginTop: 12 }}>
                {category.scope_exclusions.map((item: any) => (
                  <div key={item.field}>
                    <Typography variant="label2" weight="bold">{item.field} · {item.item}</Typography>
                    {item.source_page && <PageLink runId={run.id} page={item.source_page} pdfAvailable={!!run.files.pdf} />}
                    <Typography display="block" variant="body2">{item.reason}</Typography>
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      ))}

      {canEdit && awaitingReview && (
        <div className="panel stack" style={{ gap: 12 }}>
          <Typography variant="headline2" weight="bold">담당자 검토</Typography>
          <Typography variant="body2">승인해도 자동 검증 상태를 확인 완료로 바꾸지 않습니다. 검토 사유를 남기고 T2 분석을 진행합니다.</Typography>
          <TextArea minRows={3} maxLength={1000} value={reason} placeholder="미확인 항목을 확인했거나 수용한 근거를 입력하세요 (필수)"
            onChange={(e) => setReason(e.target.value)} />
          <div className="row">
            <Button color="negative" variant="outlined" disabled={!reason.trim() || busy} loading={busy} onClick={() => act("reject")}>T2 진행 반려</Button>
            <span className="grow" />
            <Button color="primary" disabled={!reason.trim() || busy} loading={busy} onClick={() => act("approve_and_run_t2")}>검토 승인 후 T2 실행</Button>
          </div>
        </div>
      )}
      {audit && audit.history?.length > 0 && (
        <div className="panel stack" style={{ gap: 8 }}>
          <Typography variant="headline2" weight="bold">검토 이력</Typography>
          {audit.history.map((entry: any, index: number) => (
            <Typography key={`${entry.at}-${index}`} variant="body2">
              {entry.at} · {entry.role} · {entry.action} · {entry.reason || "사유 없음"}
            </Typography>
          ))}
        </div>
      )}
    </div>
  );
}
