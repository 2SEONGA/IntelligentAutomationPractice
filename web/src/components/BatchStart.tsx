import { useEffect, useRef, useState } from "react";
import { Button, SectionMessage, SegmentedControl, SegmentedControlItem, Typography } from "@montage-ui/core";
import { getBatch, startAll, stopBatch } from "../api";
import type { BatchState } from "../types";

type Props = { ready: number; staff: boolean; jobRunning: boolean; liveAvailable: boolean; onChanged: () => void };

/** "전체 검토 시작": runs every 검토 대상 notice one after another on the server; RFP-less ones wait for an upload. */
export default function BatchStart({ ready, staff, jobRunning, liveAvailable, onChanged }: Props) {
  const [batch, setBatch] = useState<BatchState | null>(null);
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState("offline");
  const [error, setError] = useState("");
  const [seen, setSeen] = useState(false);  // show the summary only for a batch watched on this screen
  const progress = useRef(-1);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const poll = () => getBatch().then((b) => {
      setBatch(b);
      const finished = b.done.length + b.waiting.length + b.failed.length;
      if (finished !== progress.current) {
        progress.current = finished;
        onChanged();  // each finished notice changes the list and the sidebar badges
      }
      if (b.running) {
        setSeen(true);
        timer = setTimeout(poll, 1500);
      }
    }, () => undefined);
    poll();
    return () => clearTimeout(timer);
  }, [batch?.running, onChanged]);

  const start = async () => {
    setError("");
    try {
      await startAll(mode);
      setOpen(false);
      setSeen(true);
      setBatch(await getBatch());
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const running = !!batch?.running;
  const finished = batch ? batch.done.length + batch.waiting.length + batch.failed.length : 0;
  return (
    <>
      {!running && (
        <Button variant="outlined" color="primary" disabled={!staff || jobRunning || ready === 0} onClick={() => setOpen(!open)}
          title={!staff ? "사업기획 담당자만 쓸 수 있어요" : ready === 0 ? "새로 시작할 검토 대상 공고가 없어요" : undefined}>
          전체 검토 시작{ready ? ` (${ready})` : ""}
        </Button>
      )}
      {open && !running && (
        <div className="row inline-panel batch-line">
          <Typography variant="label2" weight="bold" title="RFP PDF가 없는 공고는 건너뛰고 업로드 대기로 남겨요.">검토 대상 {ready}건 차례로 실행 · RFP 없으면 업로드 대기</Typography>
          <SegmentedControl size="small" value={mode} onValueChange={setMode} sx={{ width: 300 }}>
            <SegmentedControlItem value="offline">offline · 규칙 기반 (무료)</SegmentedControlItem>
            <SegmentedControlItem value="live" disabled={!liveAvailable}>live · CrewAI</SegmentedControlItem>
          </SegmentedControl>
          {mode === "live" && (
            <Typography variant="caption1" weight="bold" color="semantic.foreground.cautionary.primary">
              최대 {ready}건만큼 OpenAI 호출 비용이 발생해요
            </Typography>
          )}
          <span className="grow" />
          <Button size="small" variant="outlined" color="assistive" onClick={() => setOpen(false)}>취소</Button>
          <Button size="small" onClick={start}>{ready}건 시작</Button>
        </div>
      )}
      {error && <div className="batch-line"><SectionMessage variant="negative">{error}</SectionMessage></div>}
      {running && batch && (
        <div className="batch-line"><SectionMessage variant="info"
          description={`${finished} / ${batch.total}건 처리 · ${batch.mode} · 지금: ${batch.current?.name ?? "준비 중"}${batch.stopped ? " · 지금 건이 끝나면 멈춰요" : ""}`}
          bottomButton={staff && !batch.stopped ? <Button size="small" variant="outlined" color="assistive" onClick={() => stopBatch().then(setBatch)}>중지</Button> : undefined}>
          전체 검토 진행 중
        </SectionMessage></div>
      )}
      {!running && seen && batch && batch.total > 0 && (
        <div className="batch-line"><SectionMessage variant={batch.failed.length ? "cautionary" : "positive"}
          description={[
            batch.waiting.length ? `RFP 업로드 대기: ${batch.waiting.map((w) => w.name).join(", ")}` : "",
            batch.failed.length ? `실패: ${batch.failed.map((f) => `${f.name} (${f.reason})`).join(", ")}` : "",
          ].filter(Boolean).join(" · ") || undefined}
          bottomButton={<Button size="small" variant="outlined" color="assistive" onClick={() => setSeen(false)}>닫기</Button>}>
          {`전체 검토 ${batch.stopped ? "중지" : "끝"} · 완료 ${batch.done.length} · 업로드 대기 ${batch.waiting.length} · 실패 ${batch.failed.length}`}
        </SectionMessage></div>
      )}
    </>
  );
}
