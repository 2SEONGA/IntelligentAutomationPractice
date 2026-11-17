const REFERENCE_KRW = 4_000_000_000; // 과거 낙찰 하한 40억 원 (docs/go_no_go_checklist.md)

// Report (Agent 4) and checklist use different words for the same four outcomes.
const MEANING: Record<string, string> = {
  Go: "go", "Go 권고": "go",
  "No-Go": "nogo", "No-Go 권고": "nogo",
  "조건부 Go": "review", "사업본부장 검토": "review",
  보류: "hold",
};

export function recommendationsDiffer(report: string, checklist: string): boolean {
  return (MEANING[report] ?? report) !== (MEANING[checklist] ?? checklist);
}

export function toneOf(recommendation: string): "Go" | "No-Go" | "보류" {
  const meaning = MEANING[recommendation];
  return meaning === "go" ? "Go" : meaning === "nogo" ? "No-Go" : "보류";
}

/** Percent of the 40억 reference, capped at 100; null when the amount is unknown. */
export function amountRatio(amountKrw: number | null | undefined): number | null {
  if (amountKrw == null) return null;
  return Math.min(100, Math.round((amountKrw / REFERENCE_KRW) * 100));
}

export const eok = (krw: number) => `${(krw / 100_000_000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억`;

/** "1~7" while undecided items could still add points, otherwise the score itself. */
export const scoreLabel = (score: number, scoreMax: number) => (scoreMax > score ? `${score}~${scoreMax}` : `${score}`);
