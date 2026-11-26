import { Typography } from "@montage-ui/core";
import type { NodeId } from "../types";
import { gateOf, type Gate, type NodeState, type Stages } from "../stages";
import StatusBadge from "./StatusBadge";

// 웹 실행은 LangGraph 한 경로(M38·M46): T1·T1.5 → 담당자 확인 → T2 → T3 → T4 → 체크리스트, 차례대로 진행한다.
// 서버 [STAGE] 이름(agent1~4)은 그대로 두고 화면 이름만 실제 단계에 맞춘다(langgraph_job.STAGE_NODES).
const LABELS: Record<NodeId, [string, string]> = {
  agent1: ["T1 · T1.5", "RFP 분석·검증"],
  agent2: ["T2", "역량 매칭"],
  agent3: ["T3", "유사 사업"],
  agent4: ["T4", "보고서 작성"],
  checklist: ["체크리스트", "Go/No-Go 판정"],
};
const STATE: Record<NodeState, [string, string]> = {
  wait: ["대기", "none"],
  run: ["실행 중…", ""],
  done: ["완료", "Go"],
  skip: ["건너뜀", "보류"],
  fail: ["실패", "No-Go"],
};

const GATE: Record<Gate, [NodeState, string, string]> = {
  wait: ["wait", "대기", "none"],
  run: ["run", "확인 중…", ""],
  hold: ["run", "담당자 확인 대기", "보류"],
  pass: ["done", "통과", "Go"],
  fail: ["fail", "중단", "No-Go"],
};

function Node({ caption, name, state, label, tone }: { caption: string; name: string; state: NodeState; label: string; tone: string }) {
  return (
    <div className={`node ${state}`} aria-label={`${name} ${label}`}>
      <Typography variant="caption1" weight="bold" color="semantic.foreground.neutral.secondary">{caption}</Typography>
      <Typography variant="headline2" weight="bold">{name}</Typography>
      <span>{state === "wait" ? <Typography variant="caption1" color="semantic.foreground.neutral.tertiary">대기</Typography> : <StatusBadge label={label} tone={tone} />}</span>
    </div>
  );
}

function Agent({ id, state, held }: { id: NodeId; state: NodeState; held: boolean }) {
  const [caption, name] = LABELS[id];
  // 담당자 확인에서 멈춘 실행의 뒷단계는 "건너뜀"이 아니라 승인 뒤에 이어서 실행된다.
  const [label, tone] = held && state === "skip" ? ["승인 후 진행", "none"] : STATE[state];
  return <Node caption={caption} name={name} state={state} label={label} tone={tone} />;
}

export default function StageTracker({ stages }: { stages: Stages }) {
  const gate = gateOf(stages);
  const held = gate === "hold";
  const [gateState, gateLabel, gateTone] = GATE[gate];
  return (
    <div className="tracker">
      <Agent id="agent1" state={stages.agent1} held={held} />
      <span className="arrow">→</span>
      <Node caption="HITL" name="담당자 확인" state={gateState} label={gateLabel} tone={gateTone} />
      <span className="arrow">→</span>
      <Agent id="agent2" state={stages.agent2} held={held} />
      <span className="arrow">→</span>
      <Agent id="agent3" state={stages.agent3} held={held} />
      <span className="arrow">→</span>
      <Agent id="agent4" state={stages.agent4} held={held} />
      <span className="arrow">→</span>
      <Agent id="checklist" state={stages.checklist} held={held} />
    </div>
  );
}
