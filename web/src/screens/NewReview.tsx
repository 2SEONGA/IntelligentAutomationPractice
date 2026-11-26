import { useId, useRef, useState, type DragEvent } from "react";
import { Button, SectionMessage, SegmentedControl, SegmentedControlItem, TextArea, TextButton, Typography } from "@montage-ui/core";
import { startRun } from "../api";
import type { Status } from "../types";
import StatusBadge from "../components/StatusBadge";

export type Draft = { rfp: File | null; notice: File | null; mode: "offline" | "live"; noticeNo: string };
export const emptyDraft: Draft = { rfp: null, notice: null, mode: "offline", noticeNo: "" };
// 나라장터 공고번호(차세대 R26BK01762350 / 구 20240112345, 차수 -000 선택). 서버도 같은 형식만 받는다.
const NOTICE_NO = /^\s*(R\d{2}[A-Z]{2}\d{8}|20\d{9})(\s*-\s*\d{3})?\s*$/i;

const isPdf = (file: File) => file.name.toLowerCase().endsWith(".pdf");
const sizeLabel = (bytes: number) => (bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)}MB` : `${Math.ceil(bytes / 1024)}KB`);

function FilePicker({ label, required, file, hint, onChange }: {
  label: string; required?: boolean; file: File | null; hint: string; onChange: (file: File | null) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const errorId = useId();
  const [error, setError] = useState("");
  const pick = (next: File | undefined) => {
    if (!next) return;
    if (!isPdf(next)) return setError("PDF 파일만 올릴 수 있어요.");
    setError("");
    onChange(next);
  };
  const drop = (event: DragEvent) => {
    event.preventDefault();
    setOver(false);
    pick(event.dataTransfer.files[0]);
  };
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="row" style={{ gap: 6 }}>
        <Typography variant="label1" weight="bold">{label}</Typography>
        <StatusBadge label={required ? "필수" : "선택"} tone={required ? "No-Go" : "none"} />
      </div>
      <input ref={input} type="file" accept=".pdf,application/pdf" hidden onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }} />
      {file ? (
        <div className="file-card">
          <span className="file-icon">PDF</span>
          <div className="grow">
            <Typography display="block" variant="label1" weight="bold">{file.name}</Typography>
            <Typography display="block" variant="caption1" color="semantic.foreground.neutral.secondary">{sizeLabel(file.size)}</Typography>
          </div>
          <Button size="small" variant="outlined" color="assistive" onClick={() => input.current?.click()}>변경</Button>
          {!required && <TextButton size="small" color="assistive" onClick={() => onChange(null)}>빼기</TextButton>}
        </div>
      ) : (
        <div className={`dropzone ${over ? "over" : ""}`} role="button" tabIndex={0} aria-label={`PDF 파일 선택 · ${hint}`} aria-describedby={error ? errorId : undefined}
          onClick={() => input.current?.click()}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.current?.click(); } }}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={drop}>
          <Typography display="block" variant="body2" color="semantic.foreground.neutral.secondary">
            PDF를 끌어다 놓거나 <b style={{ color: "var(--semantic-foreground-brand-primary)" }}>파일 선택</b> · {hint}
          </Typography>
        </div>
      )}
      {error && <Typography id={errorId} role="alert" variant="caption1" color="semantic.foreground.negative.primary">{error}</Typography>}
    </div>
  );
}

type Props = {
  status: Status | null; draft: Draft; setDraft: (draft: Draft) => void; onStarted: (jobId: string) => void;
  folderId?: string; noticePdf?: boolean; noticeName?: string; notes?: string[];
};

export default function NewReview({ status, draft, setDraft, onStarted, folderId, noticePdf, noticeName, notes = [] }: Props) {
  const noticeNoInvalid = !folderId && draft.noticeNo.trim() !== "" && !NOTICE_NO.test(draft.noticeNo);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const liveAvailable = status?.live_available ?? false;
  const update = (patch: Partial<Draft>) => setDraft({ ...draft, ...patch });

  const submit = async () => {
    if (!draft.rfp) return;
    const form = new FormData();
    form.append("rfp", draft.rfp);
    if (draft.notice) form.append("notice", draft.notice);
    if (!folderId && draft.noticeNo.trim()) form.append("notice_no", draft.noticeNo.trim());
    form.append("mode", draft.mode);
    if (folderId) form.append("folder_id", folderId);
    setSubmitting(true);
    setError("");
    try {
      onStarted((await startRun(form)).job_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <div>
        <Typography display="block" variant="title3" weight="bold">새 공고 검토</Typography>
        <Typography display="block" variant="body2" color="semantic.foreground.neutral.secondary">
          제안요청서를 올리면 핵심정보(금액·자격·일정)를 먼저 검증하고, 확인이 끝나면 회사 역량·유사 사업·Go/No-Go 권고를 만듭니다. 최종 결정은 사업본부장이 합니다.
        </Typography>
      </div>
      <div className="panel stack" style={{ gap: 20 }}>
        <FilePicker label="제안요청서 PDF" required file={draft.rfp} hint="최대 50MB" onChange={(rfp) => update({ rfp })} />
        {folderId ? (
          <SectionMessage variant="info" description={noticePdf
            ? `공고문 PDF는 나라장터 첨부에서 받았어요. 첨부에서 RFP PDF를 받지 못해 제안요청서만 직접 올려 주세요.${notes.length ? " " + notes.join(" · ") : ""}`
            : `공고 첨부에서 PDF를 받지 못했어요. 제안요청서를 PDF로 올려 주세요.${notes.length ? " " + notes.join(" · ") : ""}`}>
            {noticeName ?? "공고"} · 제안요청서 PDF를 올려 주세요
          </SectionMessage>
        ) : (
          <>
            <FilePicker label="입찰공고문 PDF" file={draft.notice}
              hint="없으면 참가자격·공고번호가 '확인 필요'로 남아 보류가 나올 수 있어요" onChange={(notice) => update({ notice })} />
            <label className="stack" style={{ gap: 6 }}>
              <Typography variant="label1" weight="bold">나라장터 공고번호 (선택)</Typography>
              <TextArea minRows={1} maxLength={20} value={draft.noticeNo} placeholder="예: R26BK01762350-000"
                onChange={(e) => update({ noticeNo: e.target.value.replace(/\n/g, "") })} aria-invalid={noticeNoInvalid} />
              <Typography variant="caption1" color={noticeNoInvalid ? "semantic.foreground.negative.primary" : "semantic.foreground.neutral.secondary"}>
                {noticeNoInvalid ? "나라장터 공고번호 형식이 아니에요 (기관 자체 번호 말고 R26BK… 번호)"
                  : "넣으면 나라장터 조회로 금액·자격·마감을 교차검증해요. 없으면 조회를 건너뜁니다."}
              </Typography>
            </label>
          </>
        )}
        <div className="row" style={{ alignItems: "flex-start", gap: 32 }}>
          <div className="stack" style={{ gap: 8 }}>
            <Typography variant="label1" weight="bold">실행 방식</Typography>
            <SegmentedControl sx={{ width: 460 }} value={draft.mode} onValueChange={(mode) => update({ mode: mode as Draft["mode"] })} size="medium">
              <SegmentedControlItem value="offline">offline · 규칙 기반 (무료)</SegmentedControlItem>
              <SegmentedControlItem value="live" disabled={!liveAvailable}>live · CrewAI + OpenAI</SegmentedControlItem>
            </SegmentedControl>
            {!liveAvailable && <Typography variant="caption1" color="semantic.foreground.neutral.secondary">.env에 OPENAI_API_KEY가 없어 live를 쓸 수 없어요</Typography>}
            {draft.mode === "live" && <Typography variant="caption1" color="semantic.foreground.cautionary.primary">live는 OpenAI 호출 비용이 발생해요</Typography>}
          </div>
          <div className="stack" style={{ gap: 8 }}>
            <Typography variant="label1" weight="bold">유사 사업·낙찰 정보</Typography>
            <Typography variant="caption1" color="semantic.foreground.neutral.secondary">
              과거 사업 색인에서 유사 사업과 연결된 낙찰정보를 함께 보여 줘요(실시간 조회 아님).
            </Typography>
          </div>
        </div>
        {error && <SectionMessage variant="negative" description={error}>검토를 시작하지 못했어요</SectionMessage>}
        <div className="row">
          <Typography className="grow" variant="caption1" color="semantic.foreground.neutral.secondary">
            회사 역량 자료: ㈜아이티센엔텍 공개 근거 (data/company) · 체크리스트: {status?.checklist_version ?? "-"}판
          </Typography>
          <Button size="large" disabled={!draft.rfp || submitting || noticeNoInvalid} loading={submitting} onClick={submit}>검토 시작</Button>
        </div>
      </div>
    </>
  );
}
