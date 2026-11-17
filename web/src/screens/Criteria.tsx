import { useEffect, useState } from "react";
import { Button, Loading, SectionMessage, TextArea, Typography } from "@montage-ui/core";
import { applyCriteriaSet, deleteCriteriaSet, getCriteria, saveCriteriaSet } from "../api";
import { DEFAULT_SET, formErrors, fromValues, toValues, type CriteriaForm } from "../criteria";
import { can, type Role } from "../role";
import { eok } from "../summary";
import type { Criteria as Data } from "../types";
import InfoBar from "../components/InfoBar";
import LoadError from "../components/LoadError";

type Lane = { tone: "pos" | "caut" | "neg"; title: string; note: string; items: string[] };
type Knockout = { id: string; title: string; question: string };
type Scoring = { id: string; title: string; levels: Record<string, string> };
type DataLimit = { title: string; missing: string; impact: string; response: string };

function Lanes({ lanes }: { lanes: Lane[] }) {
  return (
    <div className="lanes">
      {lanes.map((lane) => (
        <div key={lane.title} className={`lane ${lane.tone}`}>
          <Typography display="block" variant="headline2" weight="bold">{lane.title}</Typography>
          <Typography display="block" variant="caption1" color="semantic.foreground.neutral.secondary">{lane.note}</Typography>
          <ul>{lane.items.map((item) => <li key={item}><Typography variant="body2">{item}</Typography></li>)}</ul>
        </div>
      ))}
    </div>
  );
}

function ScoreBand({ go, review }: { go: number; review: number }) {
  const cells = Array.from({ length: 11 }, (_, n) => n);
  const tone = (n: number) => (n >= go ? "pos" : n >= review ? "caut" : "neg");
  return (
    <div className="stack" style={{ gap: 6 }}>
      <div className="score-band">
        {cells.map((n) => <span key={n} className={`score-cell ${tone(n)}`}>{n}</span>)}
      </div>
      <div className="score-band legend">
        <span className="neg" style={{ gridColumn: `1 / span ${review}` }}>0~{review - 1}점 · No-Go 권고</span>
        <span className="caut" style={{ gridColumn: `${review + 1} / span ${go - review}` }}>{review}~{go - 1}점 · 사업본부장 검토</span>
        <span className="pos" style={{ gridColumn: `${go + 1} / span ${11 - go}` }}>{go}~10점 · Go 권고</span>
      </div>
    </div>
  );
}

const FIELDS: { key: keyof CriteriaForm; label: string; hint?: string; wide?: boolean }[] = [
  { key: "minEok", label: "사업금액 하한 (억 원)" },
  { key: "warnDays", label: "마감 경고 일수" },
  { key: "go", label: "Go 권고 점수 (이상)" },
  { key: "review", label: "본부장 검토 점수 (이상)" },
  { key: "fieldKeywords", label: "분야 키워드", hint: "조달 분류가 비었을 때 공고명으로 판정", wide: true },
  { key: "excludeTerms", label: "제외 계약방식", wide: true },
  { key: "confirmTerms", label: "확인 필요 계약방식", wide: true },
  { key: "s2High", label: "S2 2점 키워드 (주력 유형)", wide: true },
  { key: "s2Mid", label: "S2 1점 키워드", wide: true },
  { key: "agencies", label: "S3 핵심 발주기관", wide: true },
];

function Editor({ form, isNew, onCancel, onSaved }: { form: CriteriaForm; isNew: boolean; onCancel: () => void; onSaved: (name: string) => void }) {
  const [draft, setDraft] = useState(form);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const errors = formErrors(draft);
  const save = async () => {
    setBusy(true);
    setError("");
    try {
      onSaved((await saveCriteriaSet(draft.name, toValues(draft))).name);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };
  return (
    <div className="inline-panel stack" style={{ gap: 12 }}>
      <Typography variant="label1" weight="bold">{isNew ? "새 판단 기준" : `"${draft.name}" 편집`}</Typography>
      <div className="criteria-form">
        {isNew && (
          <label className="field wide">
            <Typography variant="caption1" weight="bold">이름</Typography>
            <TextArea minRows={1} maxLength={30} value={draft.name} placeholder="예: 20억 이상 공격적 수주" onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          </label>
        )}
        {FIELDS.map((f) => (
          <label key={f.key} className={`field ${f.wide ? "wide" : ""}`}>
            <Typography variant="caption1" weight="bold">{f.label}</Typography>
            <TextArea minRows={1} value={draft[f.key]} onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })} />
            {f.hint && <Typography variant="caption2" color="semantic.foreground.neutral.secondary">{f.hint}</Typography>}
          </label>
        ))}
      </div>
      <Typography variant="caption1" color="semantic.foreground.neutral.secondary">목록은 쉼표(,)로 구분해요. 나머지 규칙과 항목 문구는 기본 기준을 그대로 써요.</Typography>
      {[...errors.filter((m) => draft.name.trim() || !m.startsWith("기준 이름")), ...(error ? [error] : [])].map((m) => <SectionMessage key={m} variant="negative">{m}</SectionMessage>)}
      <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
        <Button size="small" variant="outlined" color="assistive" onClick={onCancel}>취소</Button>
        <Button size="small" disabled={errors.length > 0} loading={busy} onClick={save}>저장</Button>
      </div>
    </div>
  );
}

// Splits the awards file's one-line meta into headline + detail; falls back to the raw text if the wording changes.
function SourceTiles({ meta }: { meta: Data["awards_meta"] }) {
  const source = /^(.*?)\s*(get\w+)\s*\((.+)\)$/.exec(meta.source ?? "");
  const period = /^(.*?)\s*\((.+)\)$/.exec(meta.query_period ?? "");
  const names = /낙찰자명에\s*(.+?)\s*포함,?\s*(.*)$/.exec(meta.filter ?? "");
  const tiles: { label: string; main?: string; sub?: string; code?: string; chips?: string[]; nowrap?: boolean }[] = [
    { label: "출처", main: source ? `${source[1]} (${source[3]})` : meta.source, code: source?.[2], sub: "" },
    { label: "조회 기간", nowrap: true, main: period ? period[1] : meta.query_period, sub: period ? period[2].replace(/,\s*/g, " · ") : "" },
    { label: "그룹사 판별", chips: names ? names[1].split("·") : undefined, main: names ? "" : meta.filter,
      sub: names ? names[2].replace(/\s*\((.+)\)/, " · $1") : "" },
    { label: "수집일", nowrap: true, main: meta.collected_on, sub: "" },
  ];
  return (
    <div className="source-tiles">
      {tiles.map((t) => (
        <div key={t.label} className="source-tile">
          <Typography display="block" variant="caption1" weight="bold" color="semantic.foreground.neutral.secondary">{t.label}</Typography>
          {t.main && <Typography display="block" variant="label1" weight="bold" className={t.nowrap ? "nowrap" : ""}>{t.main}</Typography>}
          {t.chips && <span className="chips">{t.chips.map((c) => <span key={c} className="chip brand">{c}</span>)}</span>}
          {t.code && <span className="source-sub code" title={t.code}>{t.code}</span>}
          {t.sub && <span className="source-sub">{t.sub}</span>}
        </div>
      ))}
    </div>
  );
}

export default function Criteria({ role }: { role: Role }) {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<{ form: CriteriaForm; isNew: boolean } | null>(null);
  const [actionError, setActionError] = useState("");
  const load = (set?: string) => getCriteria(set).then((d) => { setData(d); setError(""); }, (e: Error) => setError(e.message));
  useEffect(() => { load(); }, []);
  const act = async (fn: () => Promise<unknown>, next?: string) => {
    setActionError("");
    try {
      await fn();
      await load(next);
    } catch (e) {
      setActionError((e as Error).message);
    }
  };
  if (error) return <LoadError title="판단 기준을 불러오지 못했어요" message={error} onRetry={() => load(data?.selected)} />;
  if (!data) return <Loading />;
  const f = data.notice_filter;
  const c = data.checklist;
  const meta = data.awards_meta;
  const flags: string[] = f.confirm_flags.map((x: { label: string }) => x.label);
  const isDefault = data.selected === DEFAULT_SET;
  const isActive = data.selected === data.active;
  const newFrom = () => setEditing({ form: { ...fromValues("", data.values) }, isNew: true });

  return (
    <>
      <div className="stack" style={{ gap: 10 }}>
        <Typography display="block" variant="title3" weight="bold">판단 기준</Typography>
        <Typography display="block" variant="body2" color="semantic.foreground.neutral.secondary">
          공고를 거르는 규칙과 Go/No-Go 체크리스트입니다. 기본 기준은 <code>config/notice_filter.yaml</code>, <code>config/checklist.yaml</code>에서 오고,
          화면에서는 기본을 복제해 새 기준을 만들어요. 지금 수집과 새 검토는 <b>적용 중</b>인 기준 하나로 판정해요.
        </Typography>
        <div className="row set-bar">
          {data.sets.map((s) => (
            <button key={s.name} type="button" className={`set-chip ${s.name === data.selected ? "on" : ""}`}
              onClick={() => { setEditing(null); setActionError(""); load(s.name); }}>
              {s.name}{s.active && <span className="set-active">적용 중</span>}
            </button>
          ))}
          <Button size="small" variant="outlined" color="primary" onClick={newFrom}>＋ 새 기준</Button>
        </div>
        <div className="row" style={{ gap: 8 }}>
          <Typography className="grow" variant="caption1" color="semantic.foreground.neutral.secondary">
            {isDefault ? "기본 기준은 보기만 할 수 있어요. 바꾸려면 복제해 새 기준을 만드세요." : `"${data.selected}" 기준 · 아래 값으로 판정해요.`}
            {!isActive && !can(role, "applyCriteria") && " 적용은 사업본부장이 해요."}
          </Typography>
          {isDefault
            ? <Button size="small" variant="outlined" color="assistive" onClick={newFrom}>복제해서 새 기준</Button>
            : <>
              <Button size="small" variant="outlined" color="assistive" onClick={newFrom}>복제</Button>
              <Button size="small" variant="outlined" color="assistive" onClick={() => setEditing({ form: fromValues(data.selected, data.values), isNew: false })}>편집</Button>
              <Button size="small" variant="outlined" color="negative" disabled={isActive}
                onClick={() => act(() => deleteCriteriaSet(data.selected))}>삭제</Button>
            </>}
          {!isActive && can(role, "applyCriteria") && (
            <Button size="small" onClick={() => act(() => applyCriteriaSet(data.selected), data.selected)}>이 기준 적용</Button>
          )}
        </div>
        {actionError && <SectionMessage variant="negative">{actionError}</SectionMessage>}
        {editing && <Editor key={`${editing.isNew}-${data.selected}`} form={editing.form} isNew={editing.isNew}
          onCancel={() => setEditing(null)} onSaved={(name) => { setEditing(null); load(name); }} />}
        <InfoBar items={[
          { label: "체크리스트", value: `${c.version}판` },
          { label: "근거 데이터", value: `그룹 낙찰 ${meta.count}건` },
          { label: "조회 범위", value: `용역 낙찰 ${(meta.scanned_service_awards ?? 0).toLocaleString("ko-KR")}건 · ${meta.query_period ?? ""}` },
        ]} />
      </div>

      <div className="panel stack" style={{ gap: 12 }}>
        <div className="row">
          <Typography className="grow" variant="headline2" weight="bold">1. 공고 규칙 필터</Typography>
          <span className="priority">
            <span className="chip negative">제외</span><span className="arrow-gt">›</span>
            <span className="chip cautionary">확인 필요</span><span className="arrow-gt">›</span>
            <span className="chip positive">검토 대상</span>
          </span>
        </div>
        <Typography variant="caption1" color="semantic.foreground.neutral.secondary">
          규칙마다 판정한 뒤 가장 강한 판정을 따라요. 판단할 값이 없으면 제외하지 않고 담당자 확인으로 보내요.
        </Typography>
        <Lanes lanes={[
          { tone: "pos", title: "검토 대상", note: "모든 조건을 만족하면", items: [
            `사업금액 ${eok(f.min_amount_krw)} 이상 (배정예산, 없으면 추정가격)`,
            `조달 분류 ${f.ict_classes.join(", ")} (분류가 없으면 공고명 키워드)`,
            `마감까지 ${f.deadline_warn_days}일 넘게 남음`,
            `${f.exclude_contract_terms.join(", ")} 아님`,
          ] },
          { tone: "caut", title: "담당자 확인 필요", note: "공고문 원문을 보고 포함·제외 (HITL-2)", items: [
            "사업금액 정보 없음",
            `마감일 없음 또는 ${f.deadline_warn_days}일 이내`,
            ...f.confirm_contract_terms.map((t: string) => `${t} 공고`),
            ...flags.map((t) => `${t} 있음`),
          ] },
          { tone: "neg", title: "제외", note: "하나라도 해당하면", items: [
            `사업금액 ${eok(f.min_amount_krw)} 미만`,
            "마감 지남",
            "ICT 분야 아님",
            ...f.exclude_contract_terms.map((t: string) => `${t}`),
          ] },
        ]} />
        <div className="row" style={{ gap: 8 }}>
          <Typography variant="caption1" weight="bold" color="semantic.foreground.neutral.secondary">수집 키워드</Typography>
          <span className="chips">{f.collect_keywords.length ? f.collect_keywords.map((k: string) => <span key={k} className="chip brand">{k}</span>)
            : <span className="chip brand">없음 · 전체 수집</span>}</span>
          <Typography variant="caption1" color="semantic.foreground.neutral.secondary">· 기본 최근 {f.default_days}일</Typography>
        </div>
      </div>

      <div className="panel stack" style={{ gap: 14 }}>
        <Typography variant="headline2" weight="bold">2. Go/No-Go 체크리스트</Typography>
        <div className="row" style={{ gap: 8 }}>
          <span className="step-badge">1단계</span>
          <Typography variant="label1" weight="bold">참가 가능 여부</Typography>
          <Typography variant="caption1" color="semantic.foreground.neutral.secondary">하나라도 미충족이면 No-Go · 확인 필요가 있으면 보류</Typography>
        </div>
        <div className="kgrid">
          {(c.knockout as Knockout[]).map((k) => (
            <div key={k.id} className="kcard">
              <span className="kid">{k.id}</span>
              <Typography display="block" variant="label1" weight="bold">{k.title}</Typography>
              <Typography display="block" variant="caption1" color="semantic.foreground.neutral.secondary">{k.question}</Typography>
            </div>
          ))}
        </div>

        <div className="row" style={{ gap: 8, marginTop: 6 }}>
          <span className="step-badge">2단계</span>
          <Typography variant="label1" weight="bold">매력도·수주 가능성 점수</Typography>
          <Typography variant="caption1" color="semantic.foreground.neutral.secondary">항목마다 0~2점, 합계 10점 · 미정 항목 때문에 구간이 바뀔 수 있으면 보류</Typography>
        </div>
        <ScoreBand go={c.decision.go} review={c.decision.review} />
        <div className="sgrid">
          {(c.scoring as Scoring[]).map((s) => (
            <div key={s.id} className="kcard">
              <span className="kid">{s.id}</span>
              <Typography display="block" variant="label1" weight="bold">{s.title}</Typography>
              <div className="levels">
                {["2", "1", "0"].filter((p) => s.levels[p]).map((p) => (
                  <div key={p} className="level">
                    <span className={`pt p${p}`}>{p}점</span>
                    <Typography variant="caption1">{s.levels[p]}</Typography>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="panel stack" style={{ gap: 14 }}>
        <Typography variant="headline2" weight="bold">3. 근거 데이터와 한계</Typography>
        <div className="source-flow">
          <div><span className="num">{(meta.scanned_service_awards ?? 0).toLocaleString("ko-KR")}</span><span>용역 낙찰 조회</span></div>
          <span className="arrow-gt">›</span>
          <div><span className="num">{meta.count}</span><span>아이티센 그룹 낙찰</span></div>
          <span className="arrow-gt">›</span>
          <div><span className="num">체크리스트</span><span>항목·점수 구간 근거</span></div>
        </div>
        <SourceTiles meta={meta} />
        <div className="limit-grid">
          {(c.data_limits as DataLimit[] ?? []).map((l, i) => (
            <div key={l.title} className="limit-card">
              <div className="row" style={{ gap: 8 }}>
                <span className="limit-no">{i + 1}</span>
                <Typography variant="label1" weight="bold">{l.title}</Typography>
              </div>
              <div className="limit-row"><span className="chip cautionary">빠진 것</span><Typography variant="body2">{l.missing}</Typography></div>
              <div className="limit-row"><span className="chip negative">영향</span><Typography variant="body2">{l.impact}</Typography></div>
              <div className="limit-row"><span className="chip positive">대응</span><Typography variant="body2">{l.response}</Typography></div>
            </div>
          ))}
        </div>
      </div>
      <SectionMessage variant="info">{data.role_note}</SectionMessage>
    </>
  );
}
