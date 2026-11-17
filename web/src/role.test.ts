import { test } from "node:test";
import assert from "node:assert/strict";
import { can, loadRole, saveRole } from "./role.ts";

test("permission table follows spec 4.1", () => {
  const staffOnly = ["collect", "reviewNotice", "startReview", "upload", "rerun", "editChecklist", "submit"] as const;
  for (const action of staffOnly) {
    assert.equal(can("staff", action), true, action);
    assert.equal(can("director", action), false, action);
  }
  for (const action of ["decide", "reject"] as const) {
    assert.equal(can("director", action), true, action);
    assert.equal(can("staff", action), false, action);
  }
});

test("no role until one is chosen; choosing is remembered for the session", () => {
  assert.equal(loadRole(), null);
  const store = new Map<string, string>();
  (globalThis as any).sessionStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => store.set(k, v), removeItem: (k: string) => store.delete(k) };
  saveRole("director");
  assert.equal(loadRole(), "director");
  saveRole(null);
  assert.equal(loadRole(), null);
  store.set("bid-review-role", "admin");
  assert.equal(loadRole(), null);
  delete (globalThis as any).sessionStorage;
});

test("result screen permissions combine role and approval status", async () => {
  const { resultPermissions } = await import("./role.ts");
  const staffDone = resultPermissions("staff", "검토 완료", true);
  assert.equal(staffDone.editChecklist, true);
  assert.equal(staffDone.submit, true);
  assert.equal(staffDone.decide, false);
  const staffPending = resultPermissions("staff", "결재 대기", true);
  assert.equal(staffPending.editChecklist, false, "결재 중에는 담당자도 체크리스트 잠금");
  assert.ok(staffPending.note.length > 0);
  const staffSupplement = resultPermissions("staff", "보완 요청", true);
  assert.equal(staffSupplement.resubmit, true);
  assert.equal(staffSupplement.rerun, true);
  assert.equal(staffSupplement.editChecklist, true);
  const directorPending = resultPermissions("director", "결재 대기", true);
  assert.equal(directorPending.decide, true);
  assert.equal(directorPending.reject, true);
  assert.equal(directorPending.editChecklist, false);
  assert.equal(resultPermissions("director", "결정 완료", true).decide, true, "결정 완료 뒤 변경 가능");
  assert.equal(resultPermissions("director", "결정 완료", true).reject, false);
  assert.equal(resultPermissions("director", "검토 완료", true).decide, false);
  const old = resultPermissions("staff", "검토 완료", false);
  assert.equal(old.submit || old.editChecklist || old.rerun, false, "옛 보고서는 아무 것도 못 함");
});
