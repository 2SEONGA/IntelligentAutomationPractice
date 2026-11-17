import { Typography } from "@montage-ui/core";
import type { NodeId } from "../types";
import type { NodeState, Stages } from "../stages";
import StatusBadge from "./StatusBadge";

const LABELS: Record<NodeId, [string, string]> = {
  agent1: ["AGENT 1", "RFP 분석"],
  agent2: ["AGENT 2 · 병렬", "역량 매칭"],
  agent3: ["AGENT 3 · 병렬", "시장 조사"],
  agent4: ["AGENT 4", "보고서 작성"],
  checklist: ["체크리스트", "Go/No-Go 판정"],
};
const STATE: Record<NodeState, [string, string]> = {
  wait: ["대기", "none"],
  run: ["실행 중…", ""],
  done: ["완료", "Go"],
  skip: ["건너뜀", "보류"],
  fail: ["실패", "No-Go"],
};

function Node({ id, state }: { id: NodeId; state: NodeState }) {
  const [caption, name] = LABELS[id];
  const [label, tone] = STATE[state];
  return (
    <div className={`node ${state}`} aria-label={`${name} ${label}`}>
      <Typography variant="caption1" weight="bold" color="semantic.foreground.neutral.secondary">{caption}</Typography>
      <Typography variant="headline2" weight="bold">{name}</Typography>
      <span>{state === "wait" ? <Typography variant="caption1" color="semantic.foreground.neutral.tertiary">대기</Typography> : <StatusBadge label={label} tone={tone} />}</span>
    </div>
  );
}

export default function StageTracker({ stages }: { stages: Stages }) {
  return (
    <div className="tracker">
      <Node id="agent1" state={stages.agent1} />
      <span className="arrow">→</span>
      <div className="parallel">
        <Node id="agent2" state={stages.agent2} />
        <Node id="agent3" state={stages.agent3} />
      </div>
      <span className="arrow">→</span>
      <Node id="agent4" state={stages.agent4} />
      <span className="arrow">→</span>
      <Node id="checklist" state={stages.checklist} />
    </div>
  );
}
