import { Typography } from "@montage-ui/core";
import { IconCircleCheck, IconDocumentSearch } from "@montage-ui/icon";
import { ROLE_LABEL, type Role } from "../role";

const ROLES: { role: Role; icon: typeof IconCircleCheck; summary: string; tasks: string[] }[] = [
  { role: "staff", icon: IconDocumentSearch, summary: "공고를 찾아 검토하고 결재를 올려요", tasks: [
    "나라장터 공고 수집 · 규칙 필터 확인", "AI 검토 실행, 체크리스트 확인·보완", "결재 올리기 · 반려 건 보완",
  ] },
  { role: "director", icon: IconCircleCheck, summary: "검토 결과를 보고 참여 여부를 결정해요", tasks: [
    "결재 대기 건 Go / 보류 / No-Go 결정", "반려와 보완 요청", "적용할 판단 기준 선택",
  ] },
];

/** Start screen: pick who is working before the app opens (the place a company sign-in would go). */
export default function RoleSelect({ onSelect }: { onSelect: (role: Role) => void }) {
  return (
    <div className="role-select">
      <div className="role-select-inner">
        <img src="/itcen-logo.svg" alt="ITCEN" className="role-logo" />
        <Typography display="block" variant="title2" weight="bold">공공 SI 입찰 검토</Typography>
        <Typography display="block" variant="body1" color="semantic.foreground.neutral.secondary">
          나라장터 공고 분석부터 Go/No-Go 결정까지. 어떤 역할로 시작할까요?
        </Typography>
        <div className="role-cards">
          {ROLES.map(({ role, icon: Icon, summary, tasks }) => (
            <button key={role} type="button" className="role-card" onClick={() => onSelect(role)}>
              <span className="role-icon"><Icon /></span>
              <Typography display="block" variant="headline1" weight="bold">{ROLE_LABEL[role]}</Typography>
              <Typography display="block" variant="body2" color="semantic.foreground.neutral.secondary">{summary}</Typography>
              <ul>{tasks.map((t) => <li key={t}><Typography variant="label2">{t}</Typography></li>)}</ul>
              <span className="role-go">이 역할로 시작 →</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
