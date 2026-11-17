import { useEffect, useRef, useState } from "react";
import { Button, SectionMessage, Typography } from "@montage-ui/core";
import { getStatus, jobEvents } from "../api";
import StageTracker from "../components/StageTracker";
import { afterDisconnect, initialStages, paceQueue, reduceStage } from "../stages";
import type { StageEvent } from "../types";

type Props = { jobId: string; notes?: string[]; onDone: (runId: string) => void; onRetry: () => void; onOpenReviews: () => void };

export default function JobProgress({ jobId, notes = [], onDone, onRetry, onOpenReviews }: Props) {
  const [stages, setStages] = useState(initialStages);
  const [logs, setLogs] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [gone, setGone] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const queue = useRef<StageEvent[]>([]);
  const lastShown = useRef(0);
  const doneRunId = useRef<string | null>(null);
  const logBox = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setStages(initialStages());
    setLogs([]);
    setError("");
    setGone(false);
    queue.current = [];
    doneRunId.current = null;
    const unsubscribe = jobEvents(jobId, (event) => {
      if (event.type === "stage") queue.current.push(event);
      else if (event.type === "log") setLogs((lines) => [...lines, event.text]);
      else if (event.type === "done") doneRunId.current = event.run_id;
      else if (event.type === "error") setError(event.message);
      else {
        getStatus().then((s) => [true, s.running_job_id === jobId] as const, () => [false, false] as const).then(([reachable, running]) => {
          const next = afterDisconnect(reachable, attempt, running);
          if (next === "reconnect") setAttempt(attempt + 1);
          else if (next === "gone") setGone(true);
          else setError("서버와 연결이 끊겼어요. 서버(run_web.py)가 켜져 있는지 확인한 뒤 새로고침하세요.");
        });
      }
    });
    // Reveal stage changes one by one so a few-second offline run still reads as 1 → 2·3 → 4.
    const timer = setInterval(() => {
      const { show, rest } = paceQueue(queue.current, lastShown.current, Date.now());
      if (show.length) {
        lastShown.current = Date.now();
        queue.current = rest;
        setStages((current) => show.reduce(reduceStage, current));
      } else if (!queue.current.length && doneRunId.current && Date.now() - lastShown.current > 600) {
        clearInterval(timer);
        onDone(doneRunId.current);
      }
    }, 100);
    return () => {
      unsubscribe();
      clearInterval(timer);
    };
  }, [jobId, onDone, attempt]);

  useEffect(() => {
    logBox.current?.scrollTo({ top: logBox.current.scrollHeight });
  }, [logs]);

  if (gone) return (
    <SectionMessage variant="info" description="이미 끝났거나, 서버를 다시 켜서 진행 기록이 사라진 실행이에요. 결과는 검토 목록에서 확인할 수 있어요."
      bottomButton={<Button size="small" variant="outlined" color="assistive" onClick={onOpenReviews}>검토 목록으로</Button>}>
      진행 중인 실행이 아니에요
    </SectionMessage>
  );
  return (
    <>
      <div>
        <Typography display="block" variant="title3" weight="bold">Agent 실행 중</Typography>
        <Typography display="block" variant="body2" color="semantic.foreground.neutral.secondary">
          RFP 분석이 끝나면 역량 매칭과 시장 조사가 동시에 진행되고, 두 결과를 받아 보고서를 작성합니다.
        </Typography>
      </div>
      {notes.length > 0 && (
        <SectionMessage variant="cautionary" description={notes.join(" · ")}>공고 첨부에서 확인할 점이 있어요</SectionMessage>
      )}
      <StageTracker stages={stages} />
      {error && (
        <SectionMessage variant="negative" description={error}
          bottomButton={<Button size="small" variant="outlined" color="assistive" onClick={onRetry}>다시 시도</Button>}>
          검토가 중단되었어요
        </SectionMessage>
      )}
      {/* Stages are the main signal; the raw log is folded away and opens by itself when something fails */}
      <details className="log-fold" open={!!error || undefined}>
        <summary>
          <Typography variant="label1" weight="bold">실행 로그</Typography>
          <Typography variant="caption1" color="semantic.foreground.neutral.secondary">
            {logs.length ? ` ${logs.length}줄 · 마지막: ${logs[logs.length - 1].slice(0, 60)}` : " 기다리는 중…"}
          </Typography>
        </summary>
        <div className="log" ref={logBox} aria-live="polite">
          {logs.length ? logs.join("\n") : "실행 로그를 기다리는 중…"}
        </div>
      </details>
    </>
  );
}
