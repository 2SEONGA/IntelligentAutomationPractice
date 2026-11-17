import { Fragment, useCallback, useEffect, useState } from "react";
import {
  Button, Loading, SectionMessage, SegmentedControl, SegmentedControlItem, Tab, TabList, TabListItem, TextArea, Typography,
} from "@montage-ui/core";
import { collectNotices, getCriteria, listNotices, reviewNotice, startNotice } from "../api";
import { can, type Role } from "../role";
import { eok } from "../summary";
import type { Criteria, Notice, NoticeList, Status, View } from "../types";
import { keywordLabel } from "../types";
import { findNotices, noticeAction, splitReason, startNotes, type NoticeSort } from "../notices";
import InfoBar, { type InfoItem } from "../components/InfoBar";
import StatusBadge from "../components/StatusBadge";
import Empty from "../components/Empty";
import FilterBar from "../components/FilterBar";
import BatchStart from "../components/BatchStart";
import { AMOUNT_LABEL, DEADLINE_LABEL, amountBucket, deadlineBucket } from "../filters";

type TabKey = "검토 대상" | "확인 필요" | "제외" | "started";
const TABS: [TabKey, string][] = [["검토 대상", "검토 대상"], ["확인 필요", "담당자 확인 필요"], ["제외", "제외"], ["started", "검토 중·완료"]];
const VERDICT_TONE: Record<string, string> = { "검토 대상": "Go", "확인 필요": "보류", 제외: "No-Go" };
type Pending = { id: string; kind: "include" | "exclude" | "start" };

/** One line per file type, e.g. "PDF 1" / "HWP 등 1". */
const attachmentLines = (n: Notice) => {
  const pdf = n.attachments.filter((a) => a.kind !== "other").length;
  const other = n.attachments.length - pdf;
  const lines = [pdf && `PDF ${pdf}`, other && `HWP 등 ${other}`].filter(Boolean) as string[];
  return lines.length ? lines : ["첨부 없음"];
};

type Props = { role: Role; status: Status | null; onNavigate: (view: View) => void; onChanged: () => void };

export default function Notices({ role, status, onNavigate, onChanged }: Props) {
  const [data, setData] = useState<NoticeList | null>(null);
  const [tab, setTab] = useState<TabKey>("검토 대상");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<NoticeSort>("deadline");
  const [due, setDue] = useState("all");
  const [budget, setBudget] = useState("all");
  const [days, setDays] = useState("30");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [reason, setReason] = useState("");
  const [mode, setMode] = useState("offline");

  const load = useCallback(() => listNotices().then(setData, (e: Error) => setMessage({ ok: false, text: e.message })), []);
  useEffect(() => { load(); }, [load]);
  const [rules, setRules] = useState<(Criteria["notice_filter"] & { set: string }) | null>(null);
  useEffect(() => { getCriteria().then((c) => setRules({ ...c.notice_filter, set: c.active }), () => setRules(null)); }, []);

  const refreshAll = useCallback(() => { load(); onChanged(); }, [load, onChanged]);

  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    setMessage(null);
    try {
      await fn();
    } catch (e) {
      setMessage({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const collect = () => act(async () => {
    const result = await collectNotices(Number(days));
    const v = result.by_verdict;
    setMessage({ ok: true, text: `${result.collected.toLocaleString("ko-KR")}건 수집 · 검토 대상 ${v["검토 대상"]} · 확인 필요 ${v["확인 필요"]} · 제외 ${v["제외"]}(ICT 아님 ${result.skipped_non_ict.toLocaleString("ko-KR")}건은 저장 안 함) (누적 ${result.total.toLocaleString("ko-KR")}건)` });
    await load();
    onChanged();
  });

  const confirm = (notice: Notice) => act(async () => {
    if (!pending) return;
    if (pending.kind === "start") {
      const result = await startNotice(notice.id, mode);
      const notes = startNotes(result);
      onChanged();
      if ("job_id" in result) onNavigate({ view: "job", jobId: result.job_id, notes });
      else onNavigate({ view: "new", folderId: result.folder_id, noticePdf: result.notice_pdf, noticeName: notice.bidNtceNm, notes });
      return;
    }
    await reviewNotice(notice.id, pending.kind === "include", reason);
    setPending(null);
    setReason("");
    await load();
    onChanged();
  });

  if (!data) return message ? <SectionMessage variant="negative" description={message.text}>공고를 불러오지 못했어요</SectionMessage> : <Loading />;
  const inTab = data.notices.filter((n) => (tab === "started" ? !!n.case_folder : n.verdict === tab && !n.case_folder));
  const today = new Date().toLocaleDateString("sv-SE");  // YYYY-MM-DD in local time
  const rows = findNotices(inTab.filter((n) => (due === "all" || deadlineBucket(n.judged.deadline, today) === due)
    && (budget === "all" || amountBucket(n.judged.amount_krw) === budget)), query, sort);
  const last = data.runs[data.runs.length - 1];
  const staff = can(role, "collect");
  const info: InfoItem[] = [
    { label: "최근 수집", value: last ? last.at.slice(0, 16).replace("T", " ") : "아직 없음" },
    ...(last ? [{ label: "수집 조건", value: `최근 ${last.days}일 · ${keywordLabel(last.keywords)}` }] : []),
    ...(rules ? [
      { label: "적용 기준", value: rules.set },
      { label: "검토 대상 기준", chips: [`${eok(rules.min_amount_krw)} 이상`, ...rules.ict_classes, `마감 ${rules.deadline_warn_days}일 넘게 남음`], row: true },
      { label: "제외", chips: [`${eok(rules.min_amount_krw)} 미만`, "마감 지남", "ICT 아님", ...rules.exclude_contract_terms], tone: "negative" as const, row: true },
      { label: "확인 필요", chips: [...rules.confirm_contract_terms, ...rules.confirm_flags.map((f: { label: string }) => f.label)], tone: "cautionary" as const, row: true },
    ] : []),
  ];

  return (
    <>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <div className="grow">
          <Typography display="block" variant="title3" weight="bold">공고함</Typography>
          <Typography display="block" variant="body2" color="semantic.foreground.neutral.secondary">
            나라장터 용역 입찰공고를 키워드 없이 모두 받아 규칙으로 거릅니다. 30일 기준 1~2분 걸려요.
          </Typography>
        </div>
        <SegmentedControl size="small" value={days} onValueChange={setDays} sx={{ width: 160 }}>
          <SegmentedControlItem value="7">최근 7일</SegmentedControlItem>
          <SegmentedControlItem value="30">최근 30일</SegmentedControlItem>
        </SegmentedControl>
        <Button disabled={!staff || busy || !data.notice_available} loading={busy && !pending} onClick={collect}>지금 수집</Button>
        <Button variant="outlined" color="primary" disabled={!can(role, "upload")} onClick={() => onNavigate({ view: "new" })}>＋ PDF로 새 검토</Button>
        {/* button sits in this row; its option panel and progress wrap onto full-width lines below */}
        <BatchStart ready={data.notices.filter((n) => n.verdict === "검토 대상" && !n.case_folder).length} staff={staff}
          jobRunning={!!status?.running_job_id} liveAvailable={!!status?.live_available} onChanged={refreshAll} />
      </div>
      <InfoBar items={info} />
      {!data.notice_available && <SectionMessage variant="cautionary" description=".env에 G2B_BID_NOTICE_KEY를 넣으면 수집할 수 있어요.">입찰공고 API 키가 없어요</SectionMessage>}
      {!staff && <Typography variant="caption1" color="semantic.foreground.neutral.secondary">수집·포함/제외·검토 시작은 사업기획 담당자 권한이에요.</Typography>}
      {message && <SectionMessage variant={message.ok ? "positive" : "negative"}>{message.text}</SectionMessage>}
      <Tab value={tab} onValueChange={(v) => { setTab(v as TabKey); setPending(null); }}>
        <TabList>
          {TABS.map(([key, label]) => <TabListItem key={key} value={key}>{label} {data.counts[key] - (key === "started" ? 0 : data.notices.filter((n) => n.verdict === key && n.case_folder).length)}</TabListItem>)}
        </TabList>
      </Tab>
      <FilterBar query={query} onQuery={setQuery} placeholder="공고명·기관 검색" shown={rows.length} total={inTab.length}
        groups={[
          { label: "마감", value: due, onChange: setDue, options: Object.entries(DEADLINE_LABEL).map(([value, label]) => ({ value, label })) },
          { label: "예산", value: budget, onChange: setBudget, options: Object.entries(AMOUNT_LABEL).map(([value, label]) => ({ value, label })) },
        ]}
        right={
          <SegmentedControl size="small" value={sort} onValueChange={(v) => setSort(v as NoticeSort)} sx={{ width: 200, flex: "none" }}>
            <SegmentedControlItem value="deadline">마감 임박순</SegmentedControlItem>
            <SegmentedControlItem value="amount">예산 큰 순</SegmentedControlItem>
          </SegmentedControl>
        } />
      <div className="panel table-scroll notice-box" style={{ padding: "4px 16px 16px" }}>
        {rows.length === 0 ? <Empty>{inTab.length ? "검색·필터에 맞는 공고가 없어요." : "이 탭에 공고가 없어요."}</Empty> : (
          <table className="data-table">
            <thead><tr><th style={{ minWidth: 220 }}>공고</th><th style={{ width: 90 }}>예산</th><th style={{ width: 90 }}>마감</th><th style={{ width: 200 }}>필터 판정 근거</th><th style={{ width: 110 }}>첨부</th><th style={{ width: 150 }} /></tr></thead>
            <tbody>
              {rows.map((n) => (
                <Fragment key={n.id}>
                  <tr>
                    <td>
                      <a className="title-link" href={n.bidNtceDtlUrl} target="_blank" rel="noreferrer">{n.bidNtceNm} ↗</a>
                      <Typography display="block" variant="caption1" color="semantic.foreground.neutral.secondary" sx={{ marginTop: "4px" }}>{n.dminsttNm} · {n.cntrctCnclsMthdNm}</Typography>
                    </td>
                    <td><Typography variant="label2">{n.judged.amount_krw ? eok(n.judged.amount_krw) : "—"}</Typography></td>
                    <td><Typography variant="label2">{n.judged.deadline ? n.judged.deadline.slice(5, 10).replace("-", "/") : "—"}</Typography></td>
                    <td>
                      <div className="verdict-cell">
                        <StatusBadge label={n.verdict} tone={VERDICT_TONE[n.verdict]} />
                        <div className="reasons">
                          {n.judged.criteria_set && n.judged.criteria_set !== "기본" && (
                            <Typography display="block" variant="caption2" color="semantic.foreground.brand.primary">기준: {n.judged.criteria_set}</Typography>
                          )}
                          {n.judged.reasons.map((reason) => {
                            const [head, detail] = splitReason(reason);
                            return (
                              <div key={reason}>
                                <Typography display="block" variant="caption1" weight="bold" color="semantic.foreground.neutral.primary">{head}</Typography>
                                {detail && <Typography display="block" variant="caption1" color="semantic.foreground.neutral.secondary">{detail}</Typography>}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                      {n.review && <Typography display="block" variant="caption1" color="semantic.foreground.brand.primary">
                        담당자 {n.review.include ? "포함" : "제외"}: {n.review.reason}</Typography>}
                    </td>
                    <td>{attachmentLines(n).map((line) => <Typography key={line} display="block" variant="caption1" sx={{ whiteSpace: "nowrap" }}>{line}</Typography>)}</td>
                    <td>
                      {(() => {
                        const action = noticeAction(n, staff);
                        if (action === "report") return <Button size="small" variant="outlined" color="assistive" onClick={() => onNavigate({ view: "run", runId: n.run_id! })}>보고서</Button>;
                        if (action === "resumeUpload") return (
                          <Button size="small" variant="outlined" color="assistive" onClick={() => onNavigate({
                            view: "new", folderId: n.case_folder!, noticeName: n.bidNtceNm,
                            noticePdf: n.attachments.some((a) => a.kind === "notice"),
                          })}>RFP 업로드 이어서</Button>
                        );
                        if (action === "waiting") return <Typography variant="caption1" color="semantic.foreground.neutral.secondary">RFP 업로드 대기</Typography>;
                        if (action === "locked") return <Typography variant="caption1" color="semantic.foreground.neutral.tertiary">담당자 권한</Typography>;
                        if (action === "start") return <Button size="small" onClick={() => setPending({ id: n.id, kind: "start" })}>검토 시작</Button>;
                        if (action === "review") return (
                          <span className="row" style={{ gap: 6 }}>
                            <Button size="small" variant="outlined" color="assistive" onClick={() => setPending({ id: n.id, kind: "include" })}>포함</Button>
                            <Button size="small" variant="outlined" color="assistive" onClick={() => setPending({ id: n.id, kind: "exclude" })}>제외</Button>
                          </span>
                        );
                        return null;
                      })()}
                    </td>
                  </tr>
                  {pending?.id === n.id && (
                    <tr>
                      <td colSpan={6} style={{ background: "var(--semantic-surface-neutral-secondary)" }}>
                        {pending.kind === "start" ? (
                          <div className="row">
                            <Typography variant="label1" weight="bold">실행 방식</Typography>
                            <SegmentedControl size="small" value={mode} onValueChange={setMode} sx={{ width: 300 }}>
                              <SegmentedControlItem value="offline">offline · 규칙 기반 (무료)</SegmentedControlItem>
                              <SegmentedControlItem value="live" disabled={!status?.live_available}>live · CrewAI</SegmentedControlItem>
                            </SegmentedControl>
                            {mode === "live" && <Typography variant="caption1" color="semantic.foreground.cautionary.primary">OpenAI 호출 비용이 발생해요</Typography>}
                            <span className="grow" />
                            <Typography variant="caption1" color="semantic.foreground.neutral.secondary">첨부 PDF를 받아 검토를 시작해요. RFP가 HWP뿐이면 업로드 화면으로 이어져요.</Typography>
                            <Button size="small" variant="outlined" color="assistive" onClick={() => setPending(null)}>취소</Button>
                            <Button size="small" loading={busy} onClick={() => confirm(n)}>시작</Button>
                          </div>
                        ) : (
                          <div className="row" style={{ alignItems: "flex-start" }}>
                            <Typography variant="label1" weight="bold">{pending.kind === "include" ? "검토 대상으로 포함" : "제외"} 사유</Typography>
                            <TextArea className="grow" minRows={1} maxLength={300} value={reason} placeholder="예: 공고문 확인 결과 참가자격 충족 (필수)"
                              onChange={(e) => setReason(e.target.value)} />
                            <Button size="small" variant="outlined" color="assistive" onClick={() => { setPending(null); setReason(""); }}>취소</Button>
                            <Button size="small" disabled={!reason.trim()} loading={busy} onClick={() => confirm(n)}>저장</Button>
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
