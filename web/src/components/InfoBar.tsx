import { Fragment } from "react";
import { Typography } from "@montage-ui/core";

export type InfoItem = {
  label: string;
  value?: string;
  chips?: string[];
  tone?: "brand" | "negative" | "cautionary";
  /** start a new line (e.g. long chip lists) */
  row?: boolean;
};

/** Label/value pairs and chip lists in one bordered box, instead of a long "a · b · c" sentence. */
export default function InfoBar({ items }: { items: InfoItem[] }) {
  return (
    <div className={`meta-bar ${items.filter((i) => i.row).length > 1 ? "aligned" : ""}`}>
      {items.map((item, i) => (
        <Fragment key={item.label}>
          {i > 0 && !item.row && <span className="meta-sep" />}
          <span className={`meta-item ${item.row ? "meta-row" : ""}`}>
            <Typography variant="caption1" weight="bold" color="semantic.foreground.neutral.secondary">{item.label}</Typography>
            {item.value && <Typography variant="label1" weight="bold">{item.value}</Typography>}
            {item.chips && (
              <span className="chips">
                {item.chips.map((chip) => <span key={chip} className={`chip ${item.tone ?? "brand"}`}>{chip}</span>)}
              </span>
            )}
          </span>
        </Fragment>
      ))}
    </div>
  );
}
