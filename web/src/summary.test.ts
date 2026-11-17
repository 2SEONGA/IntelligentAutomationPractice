import { test } from "node:test";
import assert from "node:assert/strict";
import { amountRatio, recommendationsDiffer, toneOf } from "./summary.ts";

test("report and checklist recommendations are compared by meaning", () => {
  assert.equal(recommendationsDiffer("보류", "보류"), false);
  assert.equal(recommendationsDiffer("Go", "Go 권고"), false);
  assert.equal(recommendationsDiffer("No-Go", "No-Go 권고"), false);
  assert.equal(recommendationsDiffer("조건부 Go", "사업본부장 검토"), false);
  assert.equal(recommendationsDiffer("보류", "No-Go 권고"), true);
  assert.equal(recommendationsDiffer("Go", "사업본부장 검토"), true);
});

test("badge tone follows the decision word", () => {
  assert.equal(toneOf("Go 권고"), "Go");
  assert.equal(toneOf("No-Go"), "No-Go");
  assert.equal(toneOf("No-Go 권고"), "No-Go");
  assert.equal(toneOf("조건부 Go"), "보류");
  assert.equal(toneOf("사업본부장 검토"), "보류");
});

test("amount bar is capped at 100% of the 40억 reference", () => {
  assert.equal(amountRatio(1_070_500_000), 27);
  assert.equal(amountRatio(10_000_000_000), 100);
  assert.equal(amountRatio(null), null);
});

test("score shows a range while items are undecided", async () => {
  const { scoreLabel } = await import("./summary.ts");
  assert.equal(scoreLabel(1, 7), "1~7");
  assert.equal(scoreLabel(6, 6), "6");
});
