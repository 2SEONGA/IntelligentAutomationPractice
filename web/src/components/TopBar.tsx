import { TextButton, Typography } from "@montage-ui/core";
import { IconPerson } from "@montage-ui/icon";
import { ROLE_LABEL, type Role } from "../role";

type Props = { role: Role; onSwitchRole: () => void; roleNote?: string };

export default function TopBar({ role, onSwitchRole, roleNote }: Props) {
  return (
    <header className="topbar">
      <img src="/itcen-logo.svg" alt="ITCEN" />
      <Typography variant="headline2" weight="bold">공공 SI 입찰 검토</Typography>
      <span className="grow" />
      <span className="user-chip" title={roleNote}>
        <span className="user-avatar"><IconPerson /></span>
        <Typography variant="label1" weight="bold">{ROLE_LABEL[role]}</Typography>
      </span>
      <TextButton size="small" color="assistive" onClick={onSwitchRole}>역할 변경</TextButton>
    </header>
  );
}
