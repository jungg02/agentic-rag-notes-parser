import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "./client";

export interface Document {
  id: number;
  course_id: number;
  original_filename: string;
  original_format: string;
  ingest_status: "pending" | "converting" | "parsing" | "embedding" | "ready" | "failed";
  ingest_error: string | null;
  page_count: number | null;
  created_at: string;
}

export function useDocuments(courseId: number) {
  return useQuery({
    queryKey: ["documents", courseId],
    queryFn: () => apiFetch<Document[]>(`/api/courses/${courseId}/documents`),
    refetchInterval: (query) => {
      const docs = query.state.data as Document[] | undefined;
      const stillIngesting = docs?.some((d) => !["ready", "failed"].includes(d.ingest_status));
      return stillIngesting ? 1500 : false;
    },
  });
}

export function useUploadDocuments(courseId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (files: FileList) => {
      const formData = new FormData();
      Array.from(files).forEach((file) => formData.append("files", file));
      return apiFetch<Document[]>(`/api/courses/${courseId}/documents`, {
        method: "POST",
        body: formData,
      });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents", courseId] }),
  });
}

export function useRetryDocument(courseId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: number) => apiFetch<Document>(`/api/documents/${documentId}/retry`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["documents", courseId] }),
  });
}
