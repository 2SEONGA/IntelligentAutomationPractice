import { Button } from "@montage-ui/core";
import { fileUrl } from "../api";
import type { RunDetail } from "../types";

export default function DownloadMenu({ run }: { run: RunDetail }) {
  const links: [string, string][] = [["report.json", "보고서 JSON"]];
  if (run.files.report_md) links.unshift(["report.md", "보고서 MD"]);
  links.push(["checklist.json", "체크리스트 결과"]);
  if (run.decision) links.push(["decision.json", "결정 기록"]);
  return (
    <div className="row" style={{ gap: 8 }}>
      {links.map(([name, label]) => (
        <Button key={name} as="a" href={fileUrl(run.id, name)} download size="small" variant="outlined" color="assistive">
          {label} ↓
        </Button>
      ))}
    </div>
  );
}
