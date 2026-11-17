import { useState } from "react";
import { Button, SectionMessage, SegmentedControl, SegmentedControlItem, TextArea, Typography } from "@montage-ui/core";
import { ApiError, rerun, transition } from "../api";
import { ROLE_LABEL, type ResultPermissions } from "../role";
import type { RunDetail, Status, WorkflowEntry } from "../types";
import StatusBadge from "./StatusBadge";

const ACTION_LABEL: Record<WorkflowEntry["action"], string> = {
  submit: "결재 올림", resubmit: "다시 결재 올림", reject: "반려", rerun: "다시 실행", decide: "결정",
};
export const STATUS_TONE: Record<string, string> = { "검토 완료": "none", "결재 대기": "", "보완 요청": "No-Go", "결정 완료": "Go" };

type Props = {
  run: RunDetail; perms: ResultPermissions; status: Status | null;
  onChanged: () => void; onJobStarted: (jobId: string) => void; onOpenCurrent?: () => void;
};

export default function WorkflowCard({ run, perms, status, onChanged, onJobStarted, onOpenCurrent }: Props) {
  const [panel, setPanel] = useState<"reject" | "rerun" | null>(null);
  const [reason, setReason] = useState("");
  const [request, setRequest] = useState("수기 보완");
  const [mode, setMode] = useState("offline");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const flow = run.workflow;

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
      setPanel(null);
      setReason("");
      onChanged();
    } catch (e) {
      const err = e as ApiError;
      setError(err.state ? `이미 처리된 건이에요: 현재 상태 ${err.state}` : err.message);
      if (err.state) onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel stack" style={{ gap: 12 }}>
      <div className="row">
        <Typography variant="headline2" weight="bold">결재</Typography>
        <StatusBadge label={flow.status} tone={STATUS_TONE[flow.status]} />
        <span className="grow" />
        {perms.submit && <Button size="small" loading={busy} onClick={() => act(() => transition(run.id, "submit"))}>결재 올리기</Button>}
        {perms.resubmit && <Button size="small" loading={busy} onClick={() => act(() => transition(run.id, "resubmit"))}>다시 결재 올리기</Button>}
        {perms.rerun && run.rerun_available && <Button size="small" variant="outlined" color="assistive" onClick={() => setPanel("rerun")}>다시 실행…</Button>}
        {perms.reject && <Button size="small" variant="outlined" color="negative" onClick={() => setPanel("reject")}>반려…</Button>}
        {!run.is_current && onOpenCurrent && <Button size="small" variant="outlined" color="assistive" onClick={onOpenCurrent}>현재 보고서로 이동</Button>}
      </div>
      {perms.note && <Typography variant="caption1" color="semantic.foreground.neutral.secondary">{perms.note}</Typography>}
      {error && <SectionMessage variant="negative">{error}</SectionMessage>}

      {panel === "reject" && (
        <div className="row inline-panel" style={{ alignItems: "flex-start" }}>
          <div className="stack" style={{ gap: 6 }}>
            <Typography variant="label2" weight="bold">요청 방식</Typography>
            <SegmentedControl size="small" value={request} onValueChange={setRequest} sx={{ width: 200 }}>
              <SegmentedControlItem value="수기 보완">수기 보완</SegmentedControlItem>
              <SegmentedControlItem value="재실행">재실행</SegmentedControlItem>
            </SegmentedControl>
          </div>
          <TextArea className="grow" minRows={2} maxLength={500} value={reason} placeholder="반려 사유 (필수) 예: 인력 증빙 보완 필요"
            onChange={(e) => setReason(e.target.value)} />
          <Button size="small" variant="outlined" color="assistive" onClick={() => setPanel(null)}>취소</Button>
          <Button size="small" color="negative" disabled={!reason.trim()} loading={busy}
            onClick={() => act(() => transition(run.id, "reject", reason, request))}>반려</Button>
        </div>
      )}
      {panel === "rerun" && (
        <div className="row inline-panel">
          <Typography variant="label2" weight="bold">실행 방식</Typography>
          <SegmentedControl size="small" value={mode} onValueChange={setMode} sx={{ width: 300 }}>
            <SegmentedControlItem value="offline">offline · 규칙 기반 (무료)</SegmentedControlItem>
            <SegmentedControlItem value="live" disabled={!status?.live_available}>live · CrewAI</SegmentedControlItem>
          </SegmentedControl>
          {mode === "live" && <Typography variant="caption1" color="semantic.foreground.cautionary.primary">OpenAI 호출 비용이 발생해요</Typography>}
          <span className="grow" />
          <Button size="small" variant="outlined" color="assistive" onClick={() => setPanel(null)}>취소</Button>
          <Button size="small" loading={busy} onClick={() => act(async () => onJobStarted((await rerun(run.id, mode)).job_id))}>다시 실행</Button>
        </div>
      )}

      {flow.history.length > 0 && (
        <ol className="timeline">
          {flow.history.map((h, i) => (
            <li key={i}>
              <Typography variant="label2" weight="bold">{ACTION_LABEL[h.action]}{h.decision ? ` · ${h.decision}` : ""}</Typography>
              <Typography variant="caption1" color="semantic.foreground.neutral.secondary">
                {" "}{ROLE_LABEL[h.role]} · {h.at.slice(0, 16).replace("T", " ")} · {h.from} → {h.to}
              </Typography>
              {(h.reason || h.request) && (
                <Typography display="block" variant="body2">{h.request ? `[${h.request}] ` : ""}{h.reason}</Typography>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
