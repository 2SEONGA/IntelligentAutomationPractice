import type { NodeId } from "./types.ts";

export type NodeState = "wait" | "run" | "done" | "skip" | "fail";
export type Stages = Record<NodeId, NodeState>;
type Change = { node: NodeId | string; state: string };

const NEXT: Record<string, NodeState> = { start: "run", done: "done", skip: "skip", fail: "fail" };

export const initialStages = (): Stages => ({ agent1: "wait", agent2: "wait", agent3: "wait", agent4: "wait", checklist: "wait" });

export function reduceStage(stages: Stages, change: Change): Stages {
  const node = change.node as NodeId;
  const next = NEXT[change.state];
  if (!(node in stages) || !next || stages[node] === "skip" || stages[node] === "fail") return stages;
  const updated = { ...stages, [node]: next };
  // The checklist is computed from Agent 4's report, so it follows Agent 4's outcome.
  if (node === "agent4" && (next === "done" || next === "skip")) updated.checklist = next;
  return updated;
}

export type Gate = "wait" | "run" | "hold" | "pass" | "fail";

/** 담당자 확인(HITL)은 서버 단계가 아니다. T1.5가 끝난 뒤 T2가 시작됐으면 통과, T2가 건너뛰어지면 승인 대기로 멈춘 것. */
export function gateOf(stages: Stages): Gate {
  if (stages.agent1 === "fail") return "fail";
  if (stages.agent1 !== "done") return "wait";
  if (stages.agent2 === "run" || stages.agent2 === "done" || stages.agent2 === "fail") return "pass";
  if (stages.agent2 === "skip") return "hold";
  return "run";
}

/** Presentation only: offline runs finish in seconds, so reveal one stage change per `minMs`. */
export function paceQueue<T>(events: T[], lastShownAt: number, now: number, minMs = 600): { show: T[]; rest: T[] } {
  if (events.length === 0 || now - lastShownAt < minMs) return { show: [], rest: events };
  return { show: events.slice(0, 1), rest: events.slice(1) };
}

export const MAX_RECONNECTS = 3;

/** After the SSE stream drops: replaying from the start is safe, so retry while the server still answers. */
/** gone: the server answers but this job is not running (finished long ago, or an old #/job/ address after a restart). */
export function afterDisconnect(serverReachable: boolean, retries: number, jobRunning: boolean): "reconnect" | "fail" | "gone" {
  if (serverReachable && retries < MAX_RECONNECTS) return "reconnect";
  return serverReachable && !jobRunning ? "gone" : "fail";
}
