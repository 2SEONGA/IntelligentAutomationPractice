import type { ReactNode } from "react";
import { Typography } from "@montage-ui/core";

export type FilterGroup = { label: string; value: string; options: { value: string; label: string }[]; onChange: (value: string) => void };

type Props = {
  query: string; onQuery: (q: string) => void; placeholder: string;
  groups?: FilterGroup[]; shown?: number; total?: number; right?: ReactNode;
};

/** Search box (stretches up to the right-hand control) + one-choice chip groups, each with an "전체" option. */
export default function FilterBar({ query, onQuery, placeholder, groups = [], shown, total, right }: Props) {
  const filtered = !!query || groups.some((g) => g.value !== "all");
  return (
    <div className="filter-bar">
      <div className="row filter-top">
        <input type="search" className="search-input" value={query} onChange={(e) => onQuery(e.target.value)}
          placeholder={placeholder} aria-label={placeholder} />
        {right}
      </div>
      {groups.length > 0 && (
        <div className="row filter-groups">
          {groups.map((g) => (
            <div key={g.label} className="filter-group" role="group" aria-label={g.label}>
              <Typography variant="caption1" weight="bold" color="semantic.foreground.neutral.secondary">{g.label}</Typography>
              {[{ value: "all", label: "전체" }, ...g.options].map((o) => (
                <button key={o.value} type="button" className={`filter-chip ${g.value === o.value ? "on" : ""}`}
                  aria-pressed={g.value === o.value} onClick={() => g.onChange(o.value)}>{o.label}</button>
              ))}
            </div>
          ))}
          {filtered && shown !== undefined && (
            <Typography variant="caption1" color="semantic.foreground.neutral.secondary">{shown} / {total}건</Typography>
          )}
        </div>
      )}
    </div>
  );
}
