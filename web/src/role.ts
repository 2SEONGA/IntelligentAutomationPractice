// Role chosen on the start screen (spec v2 4.1·4.2); a real sign-in would replace the start screen. The server enforces the same table via the X-Role header.
export type Role = "staff" | "director";
export type Action =
  | "collect" | "reviewNotice" | "startReview" | "upload" | "rerun" | "editChecklist" | "submit"
  | "decide" | "reject" | "applyCriteria";

export const ROLE_LABEL: Record<Role, string> = { staff: "사업기획 담당자", director: "사업본부장" };

const ALLOWED: Record<Action, Role> = {
  collect: "staff", reviewNotice: "staff", startReview: "staff", upload: "staff", rerun: "staff",
  editChecklist: "staff", submit: "staff", decide: "director", reject: "director", applyCriteria: "director",
};

export const can = (role: Role, action: Action) => ALLOWED[action] === role;

const KEY = "bid-review-role";

/** The role chosen on the start screen, kept per browser tab like a login session (null → show the start screen). */
export function loadRole(): Role | null {
  try {
    const saved = globalThis.sessionStorage?.getItem(KEY);
    return saved === "staff" || saved === "director" ? saved : null;
  } catch {
    return null;
  }
}

export function saveRole(role: Role | null): void {
  try {
    if (role) globalThis.sessionStorage?.setItem(KEY, role);
    else globalThis.sessionStorage?.removeItem(KEY);
  } catch {
    // private mode etc.: the choice still holds for this page view
  }
}

export type ApprovalStatus = "검토 완료" | "결재 대기" | "보완 요청" | "결정 완료";
export type ResultPermissions = {
  editChecklist: boolean; submit: boolean; resubmit: boolean; rerun: boolean; reject: boolean; decide: boolean; note: string;
};

/** What the result screen may offer, from role × approval status (server enforces the same; spec 4.1·4.5). */
export function resultPermissions(role: Role, status: ApprovalStatus, isCurrent: boolean): ResultPermissions {
  const none = { editChecklist: false, submit: false, resubmit: false, rerun: false, reject: false, decide: false };
  if (!isCurrent) return { ...none, note: "옛 보고서는 보기만 할 수 있어요. 현재 보고서에서 결재를 진행하세요." };
  const staff = role === "staff";
  const director = role === "director";
  const result = {
    editChecklist: staff && (status === "검토 완료" || status === "보완 요청"),
    submit: staff && status === "검토 완료",
    resubmit: staff && status === "보완 요청",
    rerun: staff && status === "보완 요청",
    reject: director && status === "결재 대기",
    decide: director && (status === "결재 대기" || status === "결정 완료"),
  };
  const note = staff
    ? status === "결재 대기" ? "결재 대기 중이라 체크리스트를 고칠 수 없어요. 본부장 결정을 기다려요."
      : status === "결정 완료" ? "결정이 끝난 건이에요." : ""
    : status === "검토 완료" ? "담당자가 결재를 올리면 결정할 수 있어요."
      : status === "보완 요청" ? "담당자가 보완해 다시 올리면 결정할 수 있어요." : "";
  return { ...result, note };
}
