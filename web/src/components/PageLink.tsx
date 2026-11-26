import { fileUrl } from "../api";

type Props = { runId: string; page: number | null | undefined; pdfAvailable: boolean; fileName?: string };

/**
 * `p.N ↗` opens the original PDF at that page; without the file it is plain text.
 * HWP·HWPX 공고문은 브라우저가 열 수 없어 `p.N ↓`로 파일을 내려받는다(쪽 번호는 참고용).
 */
export default function PageLink({ runId, page, pdfAvailable, fileName = "rfp.pdf" }: Props) {
  if (!page) return null;
  if (!pdfAvailable) return <span className="muted">p.{page}</span>;
  if (!fileName.toLowerCase().endsWith(".pdf")) {
    return (
      <a className="page-link" href={fileUrl(runId, fileName)} download title="HWP·HWPX 공고문은 내려받아 한글에서 열어 주세요">
        p.{page} ↓
      </a>
    );
  }
  return (
    <a className="page-link" href={`${fileUrl(runId, fileName)}#page=${page}`} target="_blank" rel="noreferrer">
      p.{page} ↗
    </a>
  );
}
