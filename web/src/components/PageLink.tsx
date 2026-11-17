import { fileUrl } from "../api";

type Props = { runId: string; page: number | null | undefined; pdfAvailable: boolean; fileName?: "rfp.pdf" | "notice.pdf" };

/** `p.N ↗` opens the original RFP at that page; without the PDF it is plain text. */
export default function PageLink({ runId, page, pdfAvailable, fileName = "rfp.pdf" }: Props) {
  if (!page) return null;
  if (!pdfAvailable) return <span className="muted">p.{page}</span>;
  return (
    <a className="page-link" href={`${fileUrl(runId, fileName)}#page=${page}`} target="_blank" rel="noreferrer">
      p.{page} ↗
    </a>
  );
}
