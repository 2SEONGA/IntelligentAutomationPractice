import { Typography } from "@montage-ui/core";
import PageLink from "../PageLink";
import StatusBadge from "../StatusBadge";

const FIELDS: [string, string][] = [
  ["project_name", "사업명"], ["procuring_entity", "발주기관"], ["demanding_entity", "수요기관"], ["notice_number", "공고번호"],
  ["budget_krw", "사업금액"], ["estimated_price_krw", "추정가격"], ["contract_period", "계약기간"], ["contract_method", "계약방법"],
  ["evaluation_method", "평가방법"], ["joint_venture_policy", "공동수급"], ["proposal_deadline", "제안서 마감"], ["bid_deadline", "입찰 마감"],
];

const show = (value: unknown) => (typeof value === "number" ? `${value.toLocaleString("ko-KR")}원` : String(value));

type Props = { rfp: Record<string, any>; runId: string; pdfAvailable: boolean };

export default function RfpTab({ rfp, runId, pdfAvailable }: Props) {
  const basic = rfp.basic_info ?? {};
  const gate = rfp.quality_gate ?? {};
  const requirements: any[] = rfp.requirements ?? [];
  return (
    <div className="stack">
      <div className="panel stack" style={{ gap: 12 }}>
        <div className="row">
          <Typography className="grow" variant="headline2" weight="bold">기본 정보</Typography>
          <Typography variant="caption1" color="semantic.foreground.neutral.secondary">문서 품질 게이트</Typography>
          <StatusBadge label={gate.status === "passed" ? "통과" : gate.status ?? "미확인"} tone={gate.status === "passed" ? "Go" : "보류"} />
        </div>
        <table className="data-table">
          <tbody>
            {FIELDS.map(([key, label]) => {
              const field = basic[key] ?? {};
              return (
                <tr key={key}>
                  <th style={{ width: 140 }}>{label}</th>
                  <td>{field.value == null
                    ? <Typography variant="body2" color="semantic.foreground.cautionary.primary">원문 확인 필요</Typography>
                    : <Typography variant="body2">{show(field.value)}</Typography>}</td>
                  <td style={{ width: 80 }}><PageLink runId={runId} page={field.source_page} pdfAvailable={pdfAvailable} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="panel stack" style={{ gap: 12 }}>
        <Typography display="block" variant="headline2" weight="bold">요구사항 {requirements.length}건</Typography>
        <table className="data-table">
          <thead><tr><th style={{ width: 110 }}>ID</th><th style={{ width: 140 }}>분류</th><th>요구사항</th><th style={{ width: 90 }}>필수</th><th style={{ width: 70 }}>근거</th></tr></thead>
          <tbody>
            {requirements.map((req, i) => (
              <tr key={`${i}-${req.id}`}>
                <td><Typography variant="label2" weight="bold">{req.id}</Typography></td>
                <td><Typography variant="label2">{req.category}</Typography></td>
                <td><Typography variant="body2">{req.name}</Typography></td>
                <td><Typography variant="label2" color="semantic.foreground.neutral.secondary">{req.mandatory_status}</Typography></td>
                <td><PageLink runId={runId} page={req.evidence?.[0]?.pdf_page} pdfAvailable={pdfAvailable} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
