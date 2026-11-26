import { test } from "node:test";
import assert from "node:assert/strict";
import { initialStages, paceQueue, reduceStage } from "./stages.ts";

test("parallel nodes run together", () => {
  let s = reduceStage(initialStages(), { node: "agent2", state: "start" });
  s = reduceStage(s, { node: "agent3", state: "start" });
  assert.equal(s.agent2, "run");
  assert.equal(s.agent3, "run");
  assert.equal(s.agent1, "wait");
});

test("skip and fail are terminal", () => {
  let s = reduceStage(initialStages(), { node: "agent4", state: "skip" });
  s = reduceStage(s, { node: "agent4", state: "start" });
  assert.equal(s.agent4, "skip");
  s = reduceStage(s, { node: "agent1", state: "fail" });
  s = reduceStage(s, { node: "agent1", state: "done" });
  assert.equal(s.agent1, "fail");
});

test("agent4 done also completes the checklist node", () => {
  const s = reduceStage(initialStages(), { node: "agent4", state: "done" });
  assert.equal(s.checklist, "done");
});

test("the 담당자 확인 gate follows T1.5 and whether T2 started", async () => {
  const { gateOf } = await import("./stages.ts");
  let s = initialStages();
  assert.equal(gateOf(s), "wait");
  s = reduceStage(s, { node: "agent1", state: "done" });
  assert.equal(gateOf(s), "run", "T1.5가 끝나고 T2 소식이 오기 전");
  assert.equal(gateOf(reduceStage(s, { node: "agent2", state: "skip" })), "hold", "T2를 건너뛰면 승인 대기로 멈춘 것");
  assert.equal(gateOf(reduceStage(s, { node: "agent2", state: "done" })), "pass");
  assert.equal(gateOf(reduceStage(initialStages(), { node: "agent1", state: "fail" })), "fail");
});

test("pace holds back bursts", () => {
  const events = [{ node: "agent1", state: "start" }, { node: "agent1", state: "done" }] as const;
  assert.deepEqual(paceQueue([...events], 1000, 1100).show, []);
  const later = paceQueue([...events], 1000, 1700);
  assert.equal(later.show.length, 1);
  assert.equal(later.rest.length, 1);
  assert.deepEqual(paceQueue([], 0, 5000), { show: [], rest: [] });
});

test("a dropped stream reconnects while the server answers, then gives up", async () => {
  const { afterDisconnect } = await import("./stages.ts");
  assert.equal(afterDisconnect(true, 0, true), "reconnect");
  assert.equal(afterDisconnect(true, 2, true), "reconnect");
  assert.equal(afterDisconnect(true, 3, true), "fail", "최대 3번 재연결");
  assert.equal(afterDisconnect(false, 0, false), "fail", "서버가 응답하지 않으면 실패로 표시");
  assert.equal(afterDisconnect(true, 0, false), "reconnect", "끝난 직후면 재연결로 결과를 받을 수 있음");
  assert.equal(afterDisconnect(true, 3, false), "gone", "서버는 켜져 있는데 실행이 없으면 끝났거나 지난 주소");
});
