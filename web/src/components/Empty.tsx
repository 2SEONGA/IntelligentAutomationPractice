import type { ReactNode } from "react";
import { Typography } from "@montage-ui/core";

/** The one "nothing here" line every list uses, so empty lists look the same on every screen. */
export default function Empty({ children }: { children: ReactNode }) {
  return <Typography display="block" variant="body2" color="semantic.foreground.neutral.secondary" className="empty-state">{children}</Typography>;
}
