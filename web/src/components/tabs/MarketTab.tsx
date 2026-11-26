import { Typography } from "@montage-ui/core";
import Empty from "../Empty";

export default function MarketTab({ market }: { market: Record<string, any> }) {
  const projects: any[] = market.similar_projects ?? [];
  const observations: any[] = market.market_observations ?? [];
  const unknown: string[] = market.unknown_items ?? [];
  return (
    <div className="stack">
      {unknown.length > 0 && (
        <div className="panel stack" style={{ gap: 8 }}>
          <Typography display="block" variant="headline2" weight="bold">확인되지 않은 사항</Typography>
          <ul className="aligned-list plain">
            {unknown.map((item, i) => <li key={i}><Typography variant="body2">{item}</Typography></li>)}
          </ul>
        </div>
      )}
      <div className="panel stack" style={{ gap: 12 }}>
        <Typography display="block" variant="headline2" weight="bold">유사 과거 사업 {projects.length}건</Typography>
        <Typography display="block" variant="caption1" color="semantic.foreground.neutral.secondary">
          T3 과거 사업 색인에서 찾은 사업과 연결된 낙찰정보예요(실시간 조회 아님).
        </Typography>
        {projects.length === 0 ? (
          <Empty>출처가 확인된 유사 사업이 없습니다.</Empty>
        ) : (
          <table className="data-table">
            <thead><tr><th>사업명</th><th>발주기관</th><th>낙찰자</th><th>금액</th><th>개찰일</th><th>출처</th></tr></thead>
            <tbody>
              {projects.map((p, i) => (
                <tr key={i}>
                  <td><Typography variant="body2">{p.project_name}</Typography></td>
                  <td><Typography variant="label2">{p.buyer ?? "—"}</Typography></td>
                  <td><Typography variant="label2">{p.awardee ?? "—"}</Typography></td>
                  <td><Typography variant="label2">{p.budget_or_award_amount ?? "—"}</Typography></td>
                  <td><Typography variant="label2">{p.opening_date?.slice(0, 10) ?? "—"}</Typography></td>
                  <td><a className="page-link" href={p.source_url} target="_blank" rel="noreferrer">나라장터 ↗</a></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {observations.length > 0 && (
        <div className="panel stack" style={{ gap: 8 }}>
          <Typography display="block" variant="headline2" weight="bold">낙찰·시장 참고</Typography>
          {observations.map((o, i) => (
            <Typography key={i} display="block" variant="body2">
              {o.statement} <a className="page-link" href={o.source_url} target="_blank" rel="noreferrer">출처 ↗</a>
            </Typography>
          ))}
        </div>
      )}
    </div>
  );
}
