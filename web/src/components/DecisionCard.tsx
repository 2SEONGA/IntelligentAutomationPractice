import { useEffect, useState } from "react";
import { Button, SectionMessage, SegmentedControl, SegmentedControlItem, TextArea, Typography } from "@montage-ui/core";
import { saveDecision } from "../api";
import type { Decision, RunDetail } from "../types";
import StatusBadge from "./StatusBadge";

type Props = { run: RunDetail; canDecide: boolean; lockNote: string; onSaved: (decision: Decision) => void };

export default function DecisionCard({ run, canDecide, lockNote, onSaved }: Props) {
  const [choice, setChoice] = useState("");
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const current = run.decision;

  useEffect(() => {
    setChoice("");
    setReason("");
    setMessage(null);
  }, [run.id]);

  const save = async () => {
    setSaving(true);
    try {
      onSaved(await saveDecision(run.id, choice, reason));
      setMessage({ ok: true, text: `'${choice}' 결정을 저장했어요.` });
      setReason("");
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="panel stack" style={{ gap: 14 }}>
      <div className="row">
        <Typography className="grow" variant="headline2" weight="bold">사업본부장 결정</Typography>
        <Typography variant="caption1" color="semantic.foreground.neutral.secondary">AI 권고와 별개로 사람이 내린 결정을 기록합니다.</Typography>
      </div>
      {current && (
        <div className="row" style={{ gap: 8 }}>
          <Typography variant="label1" weight="bold">현재 결정</Typography>
          <StatusBadge label={`${current.decision} 결정`} tone={current.decision} />
          <Typography variant="body2">{current.reason}</Typography>
          <Typography variant="caption1" color="semantic.foreground.neutral.secondary">
            {current.decided_at.slice(0, 16).replace("T", " ")}
            {current.decision_history.length > 0 && ` · 이전 결정 ${current.decision_history.length}건`}
          </Typography>
        </div>
      )}
      {!canDecide ? (
        <Typography variant="caption1" color="semantic.foreground.neutral.secondary">
          {lockNote || "결재 대기 상태에서 사업본부장이 결정합니다."}
        </Typography>
      ) : <div className="row" style={{ alignItems: "flex-start" }}>
        <SegmentedControl size="medium" value={choice} onValueChange={setChoice} sx={{ width: 280 }}>
          <SegmentedControlItem value="Go">Go</SegmentedControlItem>
          <SegmentedControlItem value="보류">보류</SegmentedControlItem>
          <SegmentedControlItem value="No-Go">No-Go</SegmentedControlItem>
        </SegmentedControl>
        <TextArea className="grow" minRows={2} maxLength={500} value={reason} placeholder="결정 사유 (필수) 예: 공고문 참가자격 확인 후 재검토"
          onChange={(e) => setReason(e.target.value)} />
        <Button size="large" disabled={!choice || !reason.trim() || saving} loading={saving} onClick={save}>
          {current ? "결정 변경" : "결정 저장"}
        </Button>
      </div>}
      {message && (
        <SectionMessage variant={message.ok ? "positive" : "negative"}>{message.text}</SectionMessage>
      )}
    </div>
  );
}
