import { Typography } from "@montage-ui/core";
import type { Report } from "../../types";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="stack" style={{ gap: 8 }}>
      <Typography display="block" variant="headline2" weight="bold">{title}</Typography>
      {children}
    </section>
  );
}

const List = ({ items }: { items: string[] }) => (
  <ul className="aligned-list">
    {items.map((item, i) => <li key={i}><Typography variant="body2">{item}</Typography></li>)}
  </ul>
);

export default function SummaryTab({ report }: { report: Report }) {
  return (
    <div className="panel stack" style={{ gap: 24 }}>
      <Section title="요약">
        <Typography display="block" variant="body1">{report.executive_summary}</Typography>
      </Section>
      {report.next_actions.length > 0 && (
        <div className="next-actions">
          <Section title="다음 할 일"><List items={report.next_actions} /></Section>
        </div>
      )}
      <Section title="핵심 발견">
        <div className="table-scroll"><table className="data-table">
          <thead><tr><th style={{ width: 160 }}>주제</th><th>평가</th><th>조치</th></tr></thead>
          <tbody>
            {report.key_findings.map((finding, i) => (
              <tr key={i}>
                <td><Typography variant="label1" weight="bold">{finding.topic}</Typography></td>
                <td><Typography variant="body2">{finding.assessment}</Typography></td>
                <td><Typography variant="body2" color="semantic.foreground.neutral.secondary">{finding.action ?? "—"}</Typography></td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </Section>
      <Section title="입찰 조건"><List items={report.bid_conditions} /></Section>
    </div>
  );
}
