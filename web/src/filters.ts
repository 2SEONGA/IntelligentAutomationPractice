// List filters shared by 공고함 · 검토 · 결재함: free-text search plus a few fixed buckets.
export const squash = (text: string) => text.toLowerCase().replace(/\s+/g, "");

/** True when the query (case/space-insensitive) is inside any of the fields; an empty query matches everything. */
export function textMatch(query: string, ...fields: (string | null | undefined)[]): boolean {
  const q = squash(query);
  return !q || squash(fields.filter(Boolean).join(" ")).includes(q);
}

export type DeadlineBucket = "past" | "week" | "twoWeeks" | "later" | "none";
export const DEADLINE_LABEL: Record<Exclude<DeadlineBucket, "past">, string> = {
  week: "7일 이내", twoWeeks: "8~14일", later: "15일 이후", none: "마감일 없음",
};

/** Calendar days from today (YYYY-MM-DD) to the deadline's date: 0~7 week, 8~14 twoWeeks. */
export function deadlineBucket(deadline: string | null, today: string): DeadlineBucket {
  if (!deadline) return "none";
  const days = Math.round((Date.parse(deadline.slice(0, 10)) - Date.parse(today)) / 86_400_000);
  return days < 0 ? "past" : days <= 7 ? "week" : days <= 14 ? "twoWeeks" : "later";
}

export type AmountBucket = "under40" | "40to100" | "100to500" | "over500" | "none";
export const AMOUNT_LABEL: Record<AmountBucket, string> = {
  under40: "40억 미만", "40to100": "40~100억", "100to500": "100~500억", over500: "500억 이상", none: "금액 없음",
};

export function amountBucket(krw: number | null | undefined): AmountBucket {
  if (krw == null) return "none";
  const eok = krw / 1e8;
  return eok < 40 ? "under40" : eok < 100 ? "40to100" : eok < 500 ? "100to500" : "over500";
}
