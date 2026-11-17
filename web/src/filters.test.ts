import { test } from "node:test";
import assert from "node:assert/strict";
import { amountBucket, deadlineBucket, textMatch } from "./filters.ts";

test("text search ignores case and spaces across several fields", () => {
  assert.equal(textMatch("ai플랫폼", "AI 플랫폼 구축", "기관"), true);
  assert.equal(textMatch(" 국세 ", "운영", "국세청"), true);
  assert.equal(textMatch("없음", "운영", null, undefined), false);
  assert.equal(textMatch("", "아무거나"), true);
});

test("deadline buckets count calendar days from today", () => {
  const today = "2026-10-29";
  assert.equal(deadlineBucket(null, today), "none");
  assert.equal(deadlineBucket("2026-10-28 10:00:00", today), "past");
  assert.equal(deadlineBucket("2026-10-29 18:00:00", today), "week");
  assert.equal(deadlineBucket("2026-11-05", today), "week");
  assert.equal(deadlineBucket("2026-11-06", today), "twoWeeks");
  assert.equal(deadlineBucket("2026-11-12", today), "twoWeeks");
  assert.equal(deadlineBucket("2026-11-13", today), "later");
});

test("amount buckets in 억 원", () => {
  assert.equal(amountBucket(null), "none");
  assert.equal(amountBucket(3_999_999_999), "under40");
  assert.equal(amountBucket(4_000_000_000), "40to100");
  assert.equal(amountBucket(10_000_000_000), "100to500");
  assert.equal(amountBucket(50_000_000_000), "over500");
});
