import { useChunkDetail } from "../../api/chunks";
import { PdfViewer } from "./PdfViewer";

interface SourcePanelProps {
  chunkId: number | null;
  onClose: () => void;
}

export function SourcePanel({ chunkId, onClose }: SourcePanelProps) {
  const { data: chunk, isLoading } = useChunkDetail(chunkId);

  if (chunkId === null) return null;

  return (
    <aside className="source-panel" role="complementary" aria-label="Source">
      <header>
        <span>{isLoading ? "Loading..." : `${chunk?.filename} — page ${chunk?.page_number}`}</span>
        <button onClick={onClose} aria-label="Close source panel">
          ×
        </button>
      </header>
      {chunk && <PdfViewer pdfUrl={chunk.pdf_url} pageNumber={chunk.page_number} />}
    </aside>
  );
}
