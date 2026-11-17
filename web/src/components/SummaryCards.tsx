import { SectionMessage, Typography } from "@montage-ui/core";
import type { RunDetail } from "../types";
import { amountRatio, eok, recommendationsDiffer, scoreLabel, toneOf } from "../summary";
import StatusBadge from "./StatusBadge";

const TONE_COLOR = {
  Go: "semantic.foreground.positive.primary",
  "No-Go": "semantic.foreground.negative.primary",
  보류: "semantic.foreground.cautionary.primary",
} as const;

function Card({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="panel stack" style={{ gap: 6 }}>
      <Typography display="block" variant="label2" weight="bold" color="semantic.foreground.neutral.secondary">{label}</Typography>
      {children}
    </div>
  );
}

export default function SummaryCards({ run }: { run: RunDetail }) {
  const report = run.report;
  const summary = run.checklist.summary;
  const amount = report.project_scale?.amount_krw ?? null;
  const ratio = amountRatio(amount);
  const tone = toneOf(report.decision_status);
  return (
    <>
      <div className="kpis">
        <Card label="보고서 권고 (Agent 4)">
          <Typography display="block" variant="title2" weight="bold" color={TONE_COLOR[tone]}>
            {report.decision_status}
          </Typography>
          <div className="row" style={{ gap: 6 }}>
            <Typography variant="caption1" color="semantic.foreground.neutral.secondary">체크리스트 권고</Typography>
            <StatusBadge label={summary.recommendation} tone={toneOf(summary.recommendation)} />
          </div>
        </Card>
        <Card label="사업금액">
          <Typography display="block" variant="title2" weight="bold">{amount == null ? "원문 확인 필요" : eok(amount)}</Typography>
          {ratio != null && (
            <>
              <div className="bar"><i style={{ width: `${ratio}%` }} /></div>
              <Typography variant="caption1" color="semantic.foreground.neutral.secondary">과거 낙찰 하한 40억의 {ratio}%</Typography>
            </>
          )}
        </Card>
        <Card label="체크리스트 1단계 · 참가 가능">
          <Typography display="block" variant="title2" weight="bold">{summary.knockout_passed} / 4</Typography>
          <Typography variant="caption1" color="semantic.foreground.neutral.secondary">
            {summary.knockout_result === "통과" ? "모두 충족" : summary.knockout_result === "No-Go" ? "미충족 항목 있음" : "담당자 확인 필요 항목 있음"}
          </Typography>
        </Card>
        <Card label="체크리스트 2단계 · 점수">
          <Typography display="block" variant="title2" weight="bold">{scoreLabel(summary.score, summary.score_max)}<Typography variant="body2" color="semantic.foreground.neutral.secondary"> / 10점</Typography></Typography>
          <Typography variant="caption1" color="semantic.foreground.neutral.secondary">
            {summary.undetermined > 0 ? `${summary.undetermined}개 항목 미정 · 확인하면 점수가 정해져요 · ` : ""}6점 이상 Go · 4~5점 검토
          </Typography>
        </Card>
      </div>
      {recommendationsDiffer(report.decision_status, summary.recommendation) && (
        <SectionMessage variant="cautionary" description={`보고서는 '${report.decision_status}', 체크리스트는 '${summary.recommendation}'입니다.`}>
          보고서 권고와 체크리스트 권고가 다릅니다. 근거를 확인하세요
        </SectionMessage>
      )}
    </>
  );
}
