import { test } from "node:test";
import assert from "node:assert/strict";
import { amountRule, basisAmount, findNotices, noticeAction, rawAmountNote, startNotes, vatBasisLabel } from "./notices.ts";

test("filter, sort and display use the VAT-normalised amount the 40억 rule compared", () => {
  const eokText = (n: number) => `${(n / 1e8).toFixed(1)}억`;
  const estimated = { amount_krw: 37e8, amount_basis_krw: 40.7e8, amount_vat_basis: "excluded", amount_source_field: "presmptPrce", deadline: null };
  assert.equal(basisAmount(estimated), 40.7e8);
  assert.equal(rawAmountNote(estimated, eokText), "추정가격 37.0억 VAT 별도 환산");
  const budget = { amount_krw: 50e8, amount_basis_krw: 50e8, amount_vat_basis: "included", amount_source_field: "asignBdgtAmt", deadline: null };
  assert.equal(rawAmountNote(budget, eokText), null);
  const unknown = { amount_krw: 50e8, amount_basis_krw: null, amount_vat_basis: "unknown", amount_source_field: "asignBdgtAmt", deadline: null };
  assert.equal(rawAmountNote(unknown, eokText), "배정예산 50.0억 · VAT 기준 확인 필요");
  assert.equal(basisAmount({ amount_krw: 12, deadline: null }), 12, "older verdicts without amount_basis_krw keep working");
  // 원금액 순(40억 > 37억)이 아니라 비교에 쓴 환산 금액 순(40.7억 > 40억)으로 정렬한다.
  const rows = [{ bidNtceNm: "a", dminsttNm: "", ntceInsttNm: "", judged: { amount_krw: 40e8, amount_basis_krw: 40e8, deadline: null } },
                { bidNtceNm: "b", dminsttNm: "", ntceInsttNm: "", judged: estimated }];
  assert.deepEqual(findNotices(rows, "", "amount").map((n) => n.bidNtceNm), ["b", "a"]);
});

test("the amount threshold always names its VAT basis", () => {
  assert.equal(amountRule("40억", "included"), "40억 이상 (VAT 포함)");
  assert.equal(amountRule("40억", "excluded", "미만"), "40억 미만 (VAT 별도)");
  assert.equal(vatBasisLabel(undefined), "VAT 기준 확인 필요");
});

test("a notice waiting for its RFP can be resumed instead of being stuck", () => {
  assert.equal(noticeAction({ verdict: "검토 대상", case_folder: "f", run_id: null }, true), "resumeUpload");
  assert.equal(noticeAction({ verdict: "검토 대상", case_folder: "f", run_id: null }, false), "waiting");
  assert.equal(noticeAction({ verdict: "검토 대상", case_folder: "f", run_id: "r" }, false), "report");
  assert.equal(noticeAction({ verdict: "검토 대상", case_folder: null, run_id: null }, true), "start");
  assert.equal(noticeAction({ verdict: "확인 필요", case_folder: null, run_id: null }, true), "review");
  assert.equal(noticeAction({ verdict: "확인 필요", case_folder: null, run_id: null }, false), "locked");
  assert.equal(noticeAction({ verdict: "제외", case_folder: null, run_id: null }, true), null);
});

test("start notes tell what was skipped or guessed", () => {
  const notes = startNotes({ skipped: ["제안요청서.pdf"], rfp_guessed: true, market_api: false, market_requested: true, notice_pdf: false });
  assert.equal(notes.length, 3);
  assert.ok(notes[0].includes("제안요청서.pdf"));
  assert.ok(notes.some((n) => n.includes("RFP")));
  assert.ok(notes.some((n) => n.includes("시장조사")));
  assert.deepEqual(startNotes({ skipped: [], rfp_guessed: false, market_api: false, market_requested: false, notice_pdf: true }), []);
});

test("a filter reason splits into a head line and a detail line", async () => {
  const { splitReason } = await import("./notices.ts");
  assert.deepEqual(splitReason("마감 임박(2026-10-28)"), ["마감 임박", "(2026-10-28)"]);
  assert.deepEqual(splitReason("제한경쟁 — 공고문 원문 확인"), ["제한경쟁", "— 공고문 원문 확인"]);
  assert.deepEqual(splitReason("40억 미만(0.4억)"), ["40억 미만", "(0.4억)"]);
  assert.deepEqual(splitReason("ICT 분야 아님(연구조사서비스)"), ["ICT 분야 아님", "(연구조사서비스)"]);
  assert.deepEqual(splitReason("제한경쟁·지역제한 — 공고문 원문 확인"), ["제한경쟁·지역제한", "— 공고문 원문 확인"]);
  assert.deepEqual(splitReason("모든 규칙 통과"), ["모든 규칙 통과"]);
  assert.deepEqual(splitReason("마감일 없음"), ["마감일 없음"]);
});

const N = (name: string, agency: string, amount: number | null, deadline: string | null) =>
  ({ bidNtceNm: name, dminsttNm: agency, ntceInsttNm: agency, judged: { amount_krw: amount, deadline } });

test("search matches title or agency, ignoring case and spaces", () => {
  const rows = [N("AI 플랫폼 구축", "한국교육학술정보원", 5e9, "2026-11-03"), N("정보시스템 운영", "국세청", 6e9, "2026-11-10")];
  assert.deepEqual(findNotices(rows, "ai플랫폼", "deadline").map((r) => r.bidNtceNm), ["AI 플랫폼 구축"]);
  assert.deepEqual(findNotices(rows, " 국세 ", "deadline").map((r) => r.bidNtceNm), ["정보시스템 운영"]);
  assert.equal(findNotices(rows, "", "deadline").length, 2);
});

test("sort by deadline (missing last) or by budget (largest first, missing last)", () => {
  const rows = [N("a", "x", 5e9, "2026-11-10"), N("b", "x", null, null), N("c", "x", 9e9, "2026-11-03")];
  assert.deepEqual(findNotices(rows, "", "deadline").map((r) => r.bidNtceNm), ["c", "a", "b"]);
  assert.deepEqual(findNotices(rows, "", "amount").map((r) => r.bidNtceNm), ["c", "a", "b"]);
  assert.deepEqual(findNotices([rows[0], rows[2]], "", "amount").map((r) => r.bidNtceNm), ["c", "a"]);
});
