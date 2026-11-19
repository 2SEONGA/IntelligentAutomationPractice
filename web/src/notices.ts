import { textMatch } from "./filters.ts";
// Notice inbox rules kept pure so they can be tested without rendering.
type NoticeLike = { verdict: string; case_folder: string | null; run_id: string | null };
export type NoticeAction = "report" | "resumeUpload" | "waiting" | "start" | "review" | "locked" | null;

/** Which control a notice row shows. A started notice without a report must stay reachable (spec 3.3, review I1). */
export function noticeAction(notice: NoticeLike, staff: boolean): NoticeAction {
  if (notice.case_folder) return notice.run_id ? "report" : staff ? "resumeUpload" : "waiting";
  if (notice.verdict === "제외") return null;
  if (!staff) return "locked";
  return notice.verdict === "검토 대상" ? "start" : notice.verdict === "확인 필요" ? "review" : null;
}

type StartInfo = { skipped: string[]; rfp_guessed: boolean; market_api: boolean; market_requested: boolean; notice_pdf: boolean };

/** Things the user should know after "검토 시작" (spec 4.4: skipped files are reported on screen). */
export function startNotes(info: StartInfo): string[] {
  const notes: string[] = [];
  if (info.skipped.length) notes.push(`받지 못한 첨부: ${info.skipped.join(", ")} (PDF가 아니거나 내려받지 못했어요)`);
  if (info.rfp_guessed) notes.push("파일 이름에 단서가 없는 PDF를 RFP로 썼어요. 결과 화면에서 RFP가 맞는지 확인하세요.");
  if (info.market_requested && !info.market_api) notes.push("공고문 PDF를 받지 못해 나라장터 시장조사를 끄고 실행했어요.");
  return notes;
}

/** VAT basis of the amount threshold (config/notice_filter.yaml min_amount_vat_basis), shown next to "40억". */
export function vatBasisLabel(basis: unknown): string {
  return basis === "included" ? "VAT 포함" : basis === "excluded" ? "VAT 별도" : "VAT 기준 확인 필요";
}

/** "40억 이상 (VAT 포함)": the threshold always names its VAT basis so 포함/별도 amounts are not mixed. */
export function amountRule(eokText: string, basis: unknown, side: "이상" | "미만" = "이상"): string {
  return `${eokText} ${side} (${vatBasisLabel(basis)})`;
}

/** "마감 임박(2026-10-28)" → ["마감 임박", "(2026-10-28)"], "제한경쟁 — 공고문 원문 확인" → ["제한경쟁", "— 공고문 원문 확인"]. */
export function splitReason(reason: string): [string] | [string, string] {
  const dash = reason.indexOf(" — ");
  if (dash > 0) return [reason.slice(0, dash), reason.slice(dash + 1)];
  const paren = reason.indexOf("(");
  if (paren > 0 && reason.endsWith(")")) return [reason.slice(0, paren).trimEnd(), reason.slice(paren)];
  return [reason];
}

export type NoticeSort = "deadline" | "amount";
type Findable = { bidNtceNm: string; dminsttNm: string; ntceInsttNm: string; judged: { amount_krw: number | null; deadline: string | null } };

/** Search by title or agency (case/space-insensitive), then sort; notices missing the sort value go last. */
export function findNotices<T extends Findable>(rows: T[], query: string, sort: NoticeSort): T[] {
  const hits = rows.filter((n) => textMatch(query, n.bidNtceNm, n.dminsttNm, n.ntceInsttNm));
  const key = (n: T) => (sort === "amount" ? -(n.judged.amount_krw ?? -Infinity) : n.judged.deadline ?? "9999");
  return [...hits].sort((a, b) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0));
}
