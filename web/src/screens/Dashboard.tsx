import { useEffect, useState } from "react";
import { Button, Loading, SectionMessage, Typography } from "@montage-ui/core";
import { getDashboard } from "../api";
import { can, type Role } from "../role";
import { eok, toneOf } from "../summary";
import type { Dashboard as Board, View } from "../types";
import InfoBar from "../components/InfoBar";
import StatusBadge from "../components/StatusBadge";
import LoadError from "../components/LoadError";
import Empty from "../components/Empty";

const DIST_COLOR: Record<string, string> = { Go: "#00BF40", "조건부 Go": "#69A5FF", 보류: "#FF9200", "No-Go": "#FF4242" };
const FUNNEL_COLOR = ["#0066FF", "#0066FF", "#4F95FF", "#FF9200", "#00BF40"];

function Card({ label, value, unit = "건", note, tone }: { label: string; value: number; unit?: string; note: string; tone?: string }) {
  return (
    <div className="panel stack" style={{ gap: 4 }}>
      <Typography variant="label2" weight="bold" color="semantic.foreground.neutral.secondary">{label}</Typography>
      <Typography display="block" variant="title2" weight="bold" sx={tone ? { color: tone } : undefined}>
        {value.toLocaleString("ko-KR")} <Typography variant="body2" color="semantic.foreground.neutral.secondary">{unit}</Typography>
      </Typography>
      <Typography variant="caption1" color="semantic.foreground.neutral.secondary">{note}</Typography>
    </div>
  );
}

type Props = { role: Role; onNavigate: (view: View) => void };

export default function Dashboard({ role, onNavigate }: Props) {
  const [board, setBoard] = useState<Board | null>(null);
  const [error, setError] = useState("");

  const load = () => getDashboard().then((b) => { setBoard(b); setError(""); }, (e: Error) => setError(e.message));
  useEffect(() => { load(); }, []);

  if (error) return <LoadError title="대시보드를 불러오지 못했어요" message={error} onRetry={load} />;
  if (!board) return <Loading />;
  const { cards, funnel, distribution, agreement, pending, deadlines } = board;
  const last = cards.last_run;
  const reviewed = Object.values(distribution).reduce((a, b) => a + b, 0);
  const maxFunnel = Math.max(1, ...funnel.map((f) => f.count));

  return (
    <>
      <div className="stack" style={{ gap: 10 }}>
        <Typography display="block" variant="title3" weight="bold">대시보드</Typography>
        {last ? (
          <InfoBar items={[
            { label: "최근 수집", value: last.at.slice(0, 16).replace("T", " ") },
            { label: "수집 기간", value: `최근 ${last.days}일` },
            last.keywords.length ? { label: "키워드", chips: last.keywords, row: true } : { label: "수집 범위", value: "전체 용역 공고" },
          ]} />
        ) : (
          <Typography display="block" variant="body2" color="semantic.foreground.neutral.secondary">아직 공고를 수집하지 않았어요</Typography>
        )}
      </div>
      {!last && (
        <SectionMessage variant="info" description="공고함에서 '지금 수집'을 누르면 나라장터 공고를 가져와 규칙으로 걸러요."
          bottomButton={<Button size="small" variant="outlined" color="assistive" onClick={() => onNavigate({ view: "notices" })}>공고함으로</Button>}>
          수집 전이에요
        </SectionMessage>
      )}
      <div className="kpis">
        <Card label="수집한 공고" value={cards.collected_total} note={`최근 수집 ${cards.collected_recent.toLocaleString("ko-KR")}건 · 누적`} />
        <Card label="규칙 필터 통과" value={cards.passed} note="40억 이상 · ICT · 마감 전" />
        <Card label="담당자 확인 필요" value={cards.confirm} note="금액·마감 없음, 참가 조건 제한" tone="var(--semantic-foreground-cautionary-primary)" />
        <Card label="결재 대기" value={cards.approvals_pending} note="본부장 결정 필요" tone={cards.approvals_pending ? "var(--semantic-foreground-negative-primary)" : undefined} />
      </div>
      <div className="grid2">
        <div className="panel stack" style={{ gap: 10 }}>
          <div className="row"><Typography className="grow" variant="headline2" weight="bold">처리 흐름</Typography>
            <Typography variant="caption1" color="semantic.foreground.neutral.secondary">공고에서 시작한 건 기준 · 3주차 To-Be 1→9단계</Typography></div>
          <div className="funnel">
            {funnel.map((f, i) => (
              <div key={f.stage} className="funnel-bar">
                <Typography variant="label1" weight="bold">{f.count.toLocaleString("ko-KR")}</Typography>
                <i style={{ height: `${Math.max(4, (f.count / maxFunnel) * 100)}%`, background: FUNNEL_COLOR[i] }} />
                <Typography variant="caption1" color="semantic.foreground.neutral.secondary">{f.stage}</Typography>
              </div>
            ))}
          </div>
        </div>
        <div className="panel stack" style={{ gap: 10 }}>
          <Typography variant="headline2" weight="bold">AI 권고 분포 · 검토 {reviewed}건</Typography>
          <div className="dist">
            {Object.entries(distribution).map(([k, v]) => v > 0 && <i key={k} style={{ width: `${(v / Math.max(1, reviewed)) * 100}%`, background: DIST_COLOR[k] }} />)}
          </div>
          <div className="row" style={{ gap: 14 }}>
            {Object.entries(distribution).map(([k, v]) => (
              <span key={k} className="legend"><i style={{ background: DIST_COLOR[k] }} /><Typography variant="caption1">{k} {v}</Typography></span>
            ))}
          </div>
          <Typography variant="caption1" color="semantic.foreground.neutral.secondary">
            전체 검토 {cards.reviews_total}건 (공고 연결 {cards.reviews_linked}건) · AI 권고와 본부장 결정 일치 {agreement.matched}/{agreement.total}
          </Typography>
        </div>
      </div>
      <div className="grid2">
        <div className="panel stack" style={{ gap: 8 }}>
          <div className="row"><Typography className="grow" variant="headline2" weight="bold">결재 대기 {pending.length}</Typography>
            <Typography variant="caption1" color="semantic.foreground.neutral.secondary">본부장만 결정할 수 있어요</Typography></div>
          {pending.length === 0 ? <Empty>결재 대기 중인 건이 없어요.</Empty> : (
            <div className="table-scroll"><table className="data-table">
              <thead><tr><th>공고</th><th>예산</th><th>AI 권고</th><th>체크리스트</th><th /></tr></thead>
              <tbody>
                {pending.map((c) => (
                  <tr key={c.run_id}>
                    <td><Typography variant="label1" weight="bold">{c.project_name}</Typography></td>
                    <td><Typography variant="label2">{c.amount_krw ? eok(c.amount_krw) : "—"}</Typography></td>
                    <td><StatusBadge label={c.ai_recommendation ?? "—"} tone={toneOf(c.ai_recommendation ?? "")} /></td>
                    <td><Typography variant="label2">{c.checklist_recommendation}</Typography></td>
                    <td>{can(role, "decide")
                      ? <Button size="small" onClick={() => onNavigate({ view: "run", runId: c.run_id })}>결정</Button>
                      : <Button size="small" variant="outlined" color="assistive" onClick={() => onNavigate({ view: "run", runId: c.run_id })}>보기</Button>}</td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
        </div>
        <div className="panel stack" style={{ gap: 8 }}>
          <Typography variant="headline2" weight="bold">마감 임박 검토 대상</Typography>
          {deadlines.length === 0 ? <Empty>마감일이 있는 검토 대상 공고가 없어요.</Empty> : (
            <div className="table-scroll"><table className="data-table">
              <tbody>
                {deadlines.map((d) => (
                  <tr key={`${d.name}-${d.deadline}`} className="clickable" onClick={() => onNavigate({ view: "notices" })}>
                    <td><button type="button" className="row-link">{d.name}</button>
                      <Typography variant="caption1" color="semantic.foreground.neutral.secondary">{d.agency} · {d.amount_krw ? eok(d.amount_krw) : "금액 없음"}</Typography></td>
                    <td style={{ width: 80 }}><StatusBadge label={d.d_day <= 0 ? "D-Day" : `D-${d.d_day}`} tone={d.d_day <= 7 ? "No-Go" : "보류"} /></td>
                  </tr>
                ))}
              </tbody>
            </table></div>
          )}
        </div>
      </div>
    </>
  );
}
