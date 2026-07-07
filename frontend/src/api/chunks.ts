import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "./client";

export interface BoundingBox {
  page_width: number;
  page_height: number;
  rects: { x0: number; y0: number; x1: number; y1: number }[];
}

export interface ChunkDetail {
  chunk_id: number;
  document_id: number;
  filename: string;
  pdf_url: string;
  page_number: number;
  bboxes: BoundingBox;
  text: string;
  context_header: string | null;
}

export function useChunkDetail(chunkId: number | null) {
  return useQuery({
    queryKey: ["chunk", chunkId],
    queryFn: () => apiFetch<ChunkDetail>(`/api/chunks/${chunkId}`),
    enabled: chunkId !== null,
  });
}
