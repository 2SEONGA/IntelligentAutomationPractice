import { useState } from "react";
import { SectionMessage, SegmentedControl, SegmentedControlItem, TextButton, Typography } from "@montage-ui/core";
import { saveChecklist } from "../../api";
import type { Checklist, ChecklistItem, ChecklistValue, RunDetail } from "../../types";
import PageLink from "../PageLink";
import StatusBadge from "../StatusBadge";
import { scoreLabel } from "../../summary";

const KNOCKOUT = ["충족", "확인 필요", "미충족"];

type Props = { run: RunDetail; pdfAvailable: boolean; editable: boolean; lockNote: string; onChange: (checklist: Checklist) => void };

export default function ChecklistTab({ run, pdfAvailable, editable, lockNote, onChange }: Props) {
  const { items, summary } = run.checklist;
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const set = async (item: ChecklistItem, value: ChecklistValue) => {
    const overrides = Object.fromEntries(items.filter((i) => i.overridden).map((i) => [i.id, i.value]));
    if (value === item.ai) delete overrides[item.id];
    else overrides[item.id] = value;
    setSaving(true);
    setError("");
    try {
      onChange(await saveChecklist(run.id, overrides));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const evidence = (item: ChecklistItem) => (
    <div className="stack" style={{ gap: 4 }}>
      <Typography variant="body2">{item.evidence} <PageLink runId={run.id} page={item.source_page} pdfAvailable={pdfAvailable} /></Typography>
      {item.overridden && (
        <span className="row" style={{ gap: 6 }}>
          <StatusBadge label="수정됨" />
          <Typography variant="caption1" color="semantic.foreground.neutral.secondary">AI 판정: {item.ai ?? "미정"}</Typography>
          <TextButton size="small" color="assistive" disabled={saving || !editable} onClick={() => set(item, item.ai)}>AI 값으로</TextButton>
        </span>
      )}
    </div>
  );

  return (
    <div className="stack">
      {!run.details_available && (
        <SectionMessage variant="cautionary" description="이 보고서의 상세 산출물은 이후 실행으로 덮어써져서, 금액 외 항목은 자동 판정 근거가 없습니다. 같은 공고의 최신 실행을 확인하세요.">
          자동 판정이 제한된 옛 보고서예요
        </SectionMessage>
      )}
      {error && <SectionMessage variant="negative" description={error}>저장하지 못했어요</SectionMessage>}
      {!editable && run.details_available && (
        <Typography variant="caption1" color="semantic.foreground.neutral.secondary">
          읽기 전용 — {lockNote || "체크리스트 수정은 사업기획 담당자 권한이에요."}
        </Typography>
      )}
      <div className="panel stack" style={{ gap: 12 }}>
        <div className="row">
          <Typography className="grow" variant="headline2" weight="bold">1단계 · 참가 가능 여부</Typography>
          <Typography variant="caption1" color="semantic.foreground.neutral.secondary">하나라도 미충족이면 No-Go, 확인 필요가 있으면 보류</Typography>
        </div>
        <table className="data-table">
          <thead><tr><th style={{ width: 200 }}>항목</th><th>AI 판단 근거</th><th style={{ width: 300 }}>판정 (담당자 수정 가능)</th></tr></thead>
          <tbody>
            {items.filter((i) => i.kind === "knockout").map((item) => (
              <tr key={item.id}>
                <td>
                  <Typography display="block" variant="label1" weight="bold">{item.id} {item.title}</Typography>
                  {item.value === "확인 필요" && <StatusBadge label="담당자 확인 필요" tone="보류" />}
                </td>
                <td>{evidence(item)}</td>
                <td>
                  <SegmentedControl size="small" value={String(item.value)} onValueChange={(v) => set(item, v)}>
                    {KNOCKOUT.map((v) => <SegmentedControlItem key={v} value={v} disabled={saving || !editable}>{v}</SegmentedControlItem>)}
                  </SegmentedControl>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="panel stack" style={{ gap: 12 }}>
        <div className="row">
          <Typography className="grow" variant="headline2" weight="bold">2단계 · 매력도·수주 가능성 {scoreLabel(summary.score, summary.score_max)} / 10점</Typography>
          <Typography variant="caption1" color="semantic.foreground.neutral.secondary">판단 기준: {run.checklist.criteria_set ?? "기본"}</Typography>
          <Typography variant="caption1" color="semantic.foreground.neutral.secondary">6점 이상 Go · 4~5점 검토 · 3점 이하 No-Go · 미정 항목으로 구간이 바뀔 수 있으면 보류</Typography>
        </div>
        <table className="data-table">
          <thead><tr><th style={{ width: 200 }}>항목</th><th>근거</th><th style={{ width: 300 }}>점수 (담당자 수정 가능)</th></tr></thead>
          <tbody>
            {items.filter((i) => i.kind === "scoring").map((item) => (
              <tr key={item.id}>
                <td><Typography variant="label1" weight="bold">{item.id} {item.title}</Typography></td>
                <td>{evidence(item)}</td>
                <td>
                  <span className="row" style={{ gap: 8 }}>
                    <span className="points" role="radiogroup" aria-label={`${item.title} 점수`}>
                      {[0, 1, 2].map((p) => (
                        <button key={p} role="radio" aria-checked={item.value === p} className={item.value === p ? "on" : ""}
                          disabled={saving || !editable} onClick={() => set(item, p)}>{p}</button>
                      ))}
                    </span>
                    {item.value == null && <StatusBadge label="미정" tone="none" />}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
