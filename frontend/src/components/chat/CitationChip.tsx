import type { Citation } from "../../api/chat";
import "./CitationChip.css";

interface CitationChipProps {
  citation: Citation;
  onOpenSource: (chunkId: number) => void;
}

export function CitationChip({ citation, onOpenSource }: CitationChipProps) {
  return (
    <button
      className="citation-chip"
      title={`${citation.filename}, page ${citation.page_number}`}
      onClick={() => onOpenSource(citation.chunk_id)}
    >
      [{citation.marker}]
    </button>
  );
}
