import type { ComponentType } from "react";
import { Typography } from "@montage-ui/core";
import { IconCircleCheck, IconDocumentSearch, IconHistory, IconInbox } from "@montage-ui/icon";
import IconDashboard from "./IconDashboard";
import IconCriteria from "./IconCriteria";
import type { Menu, View } from "../types";
import type { Role } from "../role";

export type Badges = { passed: number; running: boolean; pending: number; supplement: number };

type Item = { menu: Menu; icon: ComponentType; label: string; target: View; badge?: (b: Badges, role: Role) => { text: string; alert?: boolean } | null };

const ITEMS: Item[] = [
  { menu: "dashboard", icon: IconDashboard, label: "대시보드", target: { view: "dashboard" } },
  { menu: "notices", icon: IconInbox, label: "공고함", target: { view: "notices" }, badge: (b) => (b.passed ? { text: String(b.passed) } : null) },
  { menu: "review", icon: IconDocumentSearch, label: "검토", target: { view: "reviews" }, badge: (b) => (b.running ? { text: "진행 중" } : null) },
  {
    menu: "approvals", icon: IconCircleCheck, label: "결재함", target: { view: "approvals" },
    badge: (b, role) => role === "director"
      ? (b.pending ? { text: String(b.pending), alert: true } : null)
      : (b.supplement ? { text: `보완 ${b.supplement}`, alert: true } : null),
  },
  { menu: "decisions", icon: IconHistory, label: "의사결정 이력", target: { view: "decisions" } },
];
const CRITERIA: Item = { menu: "criteria", icon: IconCriteria, label: "판단 기준", target: { view: "criteria" } };

type Props = { active: Menu; badges: Badges; role: Role; onNavigate: (view: View) => void };

export default function Sidebar({ active, badges, role, onNavigate }: Props) {
  const render = (item: Item) => {
    const badge = item.badge?.(badges, role);
    return (
      <button key={item.menu} className={`menu-item ${active === item.menu ? "active" : ""}`} onClick={() => onNavigate(item.target)}
        title={item.label} aria-label={item.label} aria-current={active === item.menu ? "page" : undefined}>
        <span className="menu-icon" aria-hidden><item.icon /></span>
        <Typography variant="label1" weight="bold" color={active === item.menu ? "semantic.foreground.brand.primary" : "semantic.foreground.neutral.secondary"}>
          {item.label}
        </Typography>
        {badge && <span className={`menu-badge ${badge.alert ? "alert" : ""}`}>{badge.text}</span>}
      </button>
    );
  };
  return (
    <nav className="sidebar" aria-label="메뉴">
      {ITEMS.map(render)}
      <div className="menu-sep" />
      {render(CRITERIA)}
    </nav>
  );
}
