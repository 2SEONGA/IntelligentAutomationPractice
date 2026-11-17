// Criteria set editor: text inputs ↔ the editable values the server validates (spec "고칠 수 있는 값").
export const DEFAULT_SET = "기본";

export type CriteriaValues = {
  min_amount_krw: number; deadline_warn_days: number; field_keywords: string[];
  exclude_contract_terms: string[]; confirm_contract_terms: string[]; core_agencies: string[];
  s2_keywords: { "2": string[]; "1": string[] }; decision: { go: number; review: number };
};

export type CriteriaForm = {
  name: string; minEok: string; warnDays: string; fieldKeywords: string; excludeTerms: string; confirmTerms: string;
  agencies: string; s2High: string; s2Mid: string; go: string; review: string;
};

export function parseList(text: string): string[] {
  return [...new Set(text.split(/[,\n]/).map((s) => s.trim()).filter(Boolean))];
}

const join = (items: string[]) => items.join(", ");

export function fromValues(name: string, v: CriteriaValues): CriteriaForm {
  return {
    name, minEok: String(v.min_amount_krw / 1e8), warnDays: String(v.deadline_warn_days),
    fieldKeywords: join(v.field_keywords), excludeTerms: join(v.exclude_contract_terms), confirmTerms: join(v.confirm_contract_terms),
    agencies: join(v.core_agencies), s2High: join(v.s2_keywords["2"]), s2Mid: join(v.s2_keywords["1"]),
    go: String(v.decision.go), review: String(v.decision.review),
  };
}

export function toValues(f: CriteriaForm): CriteriaValues {
  return {
    min_amount_krw: Math.round(Number(f.minEok) * 1e8), deadline_warn_days: Number(f.warnDays),
    field_keywords: parseList(f.fieldKeywords), exclude_contract_terms: parseList(f.excludeTerms),
    confirm_contract_terms: parseList(f.confirmTerms), core_agencies: parseList(f.agencies),
    s2_keywords: { "2": parseList(f.s2High), "1": parseList(f.s2Mid) },
    decision: { go: Number(f.go), review: Number(f.review) },
  };
}

const intIn = (text: string, low: number, high: number) => {
  const n = Number(text.trim());
  return text.trim() !== "" && Number.isInteger(n) && n >= low && n <= high;
};

/** Same rules as the server, shown before saving. */
export function formErrors(f: CriteriaForm): string[] {
  const errors: string[] = [];
  const name = f.name.trim();
  if (!name || name === DEFAULT_SET || name.length > 30 || /[/\\:]/.test(name) || name.startsWith("."))
    errors.push("기준 이름은 1~30자이고 '기본'·/ \\ : 를 쓸 수 없어요.");
  const eok = Number(f.minEok.trim());
  if (f.minEok.trim() === "" || !Number.isFinite(eok) || eok < 0) errors.push("금액 하한은 0 이상 숫자(억 원)로 입력하세요.");
  if (!intIn(f.warnDays, 0, 30)) errors.push("마감 경고 일수는 0~30 정수로 입력하세요.");
  if (!intIn(f.go, 0, 10) || !intIn(f.review, 0, 10) || Number(f.review) >= Number(f.go))
    errors.push("점수 구간은 0~10 정수이고, 검토 점수가 Go 점수보다 낮아야 해요.");
  return errors;
}
