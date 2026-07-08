import { useChunkDetail } from "../../api/chunks";
import { PdfViewer } from "./PdfViewer";
import "./SourcePanel.css";

interface SourcePanelProps {
  chunkId: number | null;
  onClose: () => void;
}

export function SourcePanel({ chunkId, onClose }: SourcePanelProps) {
  const { data: chunk, isLoading } = useChunkDetail(chunkId);

  if (chunkId === null) return null;

  return (
    <>
      <div className="source-panel-backdrop" onClick={onClose} />
      <aside className="source-panel" role="complementary" aria-label="Source">
        <header className="source-panel-header">
          <span className="source-panel-title">
            {isLoading ? "Loading..." : `${chunk?.filename} — page ${chunk?.page_number}`}
          </span>
          <button className="source-panel-close" onClick={onClose} aria-label="Close source panel">
            ×
          </button>
        </header>
        <div className="source-panel-body">
          {chunk && <PdfViewer pdfUrl={chunk.pdf_url} pageNumber={chunk.page_number} />}
        </div>
      </aside>
    </>
  );
}
