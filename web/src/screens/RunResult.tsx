import { useCallback, useEffect, useState } from "react";
import { Loading, SectionMessage, Tab, TabList, TabListItem, TabPanel, Typography } from "@montage-ui/core";
import { getRun } from "../api";
import { resultPermissions, type Role } from "../role";
import type { RunDetail, Status, View } from "../types";
import WorkflowCard from "../components/WorkflowCard";
import DownloadMenu from "../components/DownloadMenu";
import SummaryCards from "../components/SummaryCards";
import CapabilityTab from "../components/tabs/CapabilityTab";
import CoreReviewTab from "../components/tabs/CoreReviewTab";
import ChecklistTab from "../components/tabs/ChecklistTab";
import MarketTab from "../components/tabs/MarketTab";
import RfpTab from "../components/tabs/RfpTab";
import SummaryTab from "../components/tabs/SummaryTab";
import DecisionCard from "../components/DecisionCard";

const MODE: Record<string, string> = { offline: "offline · 규칙 기반", live: "live · CrewAI" };

type Props = { runId: string; role: Role; status: Status | null; onChanged: () => void; onNavigate: (view: View) => void };

export default function RunResult({ runId, role, status, onChanged, onNavigate }: Props) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState("summary");

  const reload = useCallback(() => getRun(runId).then(setRun, (e: Error) => setError(e.message)), [runId]);
  useEffect(() => {
    setRun(null);
    setError("");
    reload();
  }, [reload]);

  if (error) return <SectionMessage variant="negative" description={error}>결과를 불러오지 못했어요</SectionMessage>;
  if (!run) return <Loading />;

  const pdf = !!run.files.pdf;
  const detailsNote = (
    <div className="panel"><Typography variant="body2" color="semantic.foreground.neutral.secondary">
      상세 산출물은 이후 실행으로 덮어써졌습니다. 같은 공고의 최신 실행에서 확인하세요.
    </Typography></div>
  );
  const perms = resultPermissions(role, run.workflow.status, run.is_current);
  const refreshAll = () => {
    reload();
    onChanged();
  };

  const decision = (
    <DecisionCard run={run} canDecide={perms.decide} onSaved={refreshAll}
      lockNote={role === "staff" ? "최종 결정은 사업본부장 권한이에요. 결재를 올리면 본부장이 결정합니다." : perms.note} />
  );
  // The decision is the point of the page once it can be made or has been made: show it before the analysis tabs.
  const decisionFirst = perms.decide || !!run.decision;

  return (
    <>
      <div className="row" style={{ alignItems: "flex-start" }}>
        <div className="grow">
          <Typography display="block" variant="title3" weight="bold">{run.project_name}</Typography>
          <Typography display="block" variant="body2" color="semantic.foreground.neutral.secondary">
            {run.created_at.slice(0, 16).replace("T", " ")} · {MODE[run.mode] ?? run.mode}{run.via_web ? " · 웹에서 실행" : ""}
            {pdf ? " · 근거 쪽 번호를 누르면 PDF 원문이 열립니다." : ""}
          </Typography>
        </div>
        <DownloadMenu run={run} />
      </div>
      <SummaryCards run={run} />
      <WorkflowCard run={run} perms={perms} status={status} onChanged={refreshAll}
        onJobStarted={(jobId) => onNavigate({ view: "job", jobId })} onOpenCurrent={() => onNavigate({ view: "reviews" })} />
      {decisionFirst && decision}
      <Tab value={tab} onValueChange={setTab}>
        <TabList>
          <TabListItem value="summary">요약</TabListItem>
          <TabListItem value="core">핵심정보 검증</TabListItem>
          <TabListItem value="rfp" disabled={!run.details_available}>RFP 분석</TabListItem>
          <TabListItem value="capability" disabled={!run.details_available}>역량 매칭</TabListItem>
          <TabListItem value="market" disabled={!run.details_available}>유사 사업·낙찰</TabListItem>
          <TabListItem value="checklist">Go/No-Go 체크리스트</TabListItem>
        </TabList>
        <TabPanel value="core" sx={{ paddingTop: 16 }}><CoreReviewTab run={run} role={role} onChanged={refreshAll}
          onOpenRun={(id) => { onChanged(); onNavigate({ view: "run", runId: id }); }} /></TabPanel>
        <TabPanel value="summary" sx={{ paddingTop: 16 }}>
          {!run.details_available && detailsNote}
          <SummaryTab report={run.report} />
        </TabPanel>
        <TabPanel value="rfp" sx={{ paddingTop: 16 }}>{run.rfp ? <RfpTab rfp={run.rfp} runId={run.id} pdfAvailable={pdf} /> : detailsNote}</TabPanel>
        <TabPanel value="capability" sx={{ paddingTop: 16 }}>{run.capability ? <CapabilityTab capability={run.capability} /> : detailsNote}</TabPanel>
        <TabPanel value="market" sx={{ paddingTop: 16 }}>{run.market ? <MarketTab market={run.market} /> : detailsNote}</TabPanel>
        <TabPanel value="checklist" sx={{ paddingTop: 16 }}>
          <ChecklistTab run={run} pdfAvailable={pdf} editable={perms.editChecklist} lockNote={perms.note} onChange={refreshAll} />
        </TabPanel>
      </Tab>
      {!decisionFirst && decision}
    </>
  );
}
