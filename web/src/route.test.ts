import { test } from "node:test";
import assert from "node:assert/strict";
import { hashToView, viewToHash } from "./route.ts";

test("every screen round-trips through the address bar", () => {
  for (const view of [
    { view: "dashboard" }, { view: "notices" }, { view: "approvals" }, { view: "decisions" }, { view: "criteria" },
    { view: "reviews" }, { view: "new" }, { view: "new", folderId: "20261027_223218_d2bf16" },
    { view: "job", jobId: "abc123" }, { view: "run", runId: "d2ViLzIw-_x" },
  ] as const) {
    assert.deepEqual(hashToView(viewToHash(view)), view, viewToHash(view));
  }
});

test("screen-only details stay out of the address", () => {
  assert.equal(viewToHash({ view: "new", folderId: "f1", noticePdf: true, noticeName: "x", notes: ["n"] }), "#/new?folder=f1");
  assert.equal(viewToHash({ view: "job", jobId: "j1", notes: ["n"] }), "#/job/j1");
});

test("unknown or empty addresses open the dashboard", () => {
  for (const hash of ["", "#", "#/", "#/nope", "#/run/", "#/job"]) assert.deepEqual(hashToView(hash), { view: "dashboard" }, hash);
});
