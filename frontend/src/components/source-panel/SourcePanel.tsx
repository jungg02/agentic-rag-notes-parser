import { useChunkDetail } from "../../api/chunks";
import { PdfViewer } from "./PdfViewer";
import "./SourcePanel.css";

// A figure has no chunk_id (it isn't a citable excerpt, ADR 013), so
// opening its source page can't go through useChunkDetail -- it already
// carries everything the panel needs (document_id/page_number/filename)
// straight from the chat "done" event, no extra lookup required.
export type SourceTarget =
  | { kind: "chunk"; chunkId: number }
  | { kind: "figure"; documentId: number; pageNumber: number; filename: string }
  | null;

interface SourcePanelProps {
  target: SourceTarget;
  onClose: () => void;
}

export function SourcePanel({ target, onClose }: SourcePanelProps) {
  const chunkId = target?.kind === "chunk" ? target.chunkId : null;
  const { data: chunk, isLoading } = useChunkDetail(chunkId);

  if (target === null) return null;

  const title =
    target.kind === "figure"
      ? `${target.filename} — page ${target.pageNumber}`
      : isLoading
        ? "Loading..."
        : `${chunk?.filename} — page ${chunk?.page_number}`;
  const pdfUrl = target.kind === "figure" ? `/api/documents/${target.documentId}/pdf` : chunk?.pdf_url;
  const pageNumber = target.kind === "figure" ? target.pageNumber : chunk?.page_number;

  return (
    <>
      <div className="source-panel-backdrop" onClick={onClose} />
      <aside className="source-panel" role="complementary" aria-label="Source">
        <header className="source-panel-header">
          <span className="source-panel-title">{title}</span>
          <button className="source-panel-close" onClick={onClose} aria-label="Close source panel">
            ×
          </button>
        </header>
        <div className="source-panel-body">
          {pdfUrl && pageNumber && <PdfViewer pdfUrl={pdfUrl} pageNumber={pageNumber} />}
        </div>
      </aside>
    </>
  );
}
