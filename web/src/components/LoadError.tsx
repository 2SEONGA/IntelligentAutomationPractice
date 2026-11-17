import { Button, SectionMessage } from "@montage-ui/core";

/** A list screen that failed to load: the reason plus a retry, so a restarted server needs no page reload. */
export default function LoadError({ title, message, onRetry }: { title: string; message: string; onRetry: () => void }) {
  return (
    <div className="stack" style={{ gap: 10, alignItems: "flex-start" }}>
      <SectionMessage variant="negative" description={message}>{title}</SectionMessage>
      <Button size="small" variant="outlined" color="assistive" onClick={onRetry}>다시 시도</Button>
    </div>
  );
}
