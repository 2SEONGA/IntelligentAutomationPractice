import type { ComponentProps } from "react";
import { ContentBadge } from "@montage-ui/core";

type ThemeColorsToken = NonNullable<ComponentProps<typeof ContentBadge>["accentColor"]>;

const COLORS: Record<string, ThemeColorsToken> = {
  Go: "semantic.foreground.positive.primary",
  보류: "semantic.foreground.cautionary.primary",
  "No-Go": "semantic.foreground.negative.primary",
};

/** Colored by the decision word inside the label: "AI: 보류", "No-Go 결정", "분석 중". */
export default function StatusBadge({ label, tone }: { label: string; tone?: string }) {
  if (tone === "none") {
    return <ContentBadge size="xsmall" variant="solid" color="neutral">{label}</ContentBadge>;
  }
  const color = COLORS[tone ?? ""] ?? "semantic.foreground.brand.primary";
  return (
    <ContentBadge size="xsmall" variant="solid" color="accent" accentColor={color}>
      {label}
    </ContentBadge>
  );
}
