import { useDocuments, useRetryDocument } from "../../api/documents";

interface DocumentListProps {
  courseId: number;
}

export function DocumentList({ courseId }: DocumentListProps) {
  const { data: documents, isLoading } = useDocuments(courseId);
  const retry = useRetryDocument(courseId);

  if (isLoading) {
    return <div>Loading documents...</div>;
  }

  return (
    <ul className="document-list">
      {(documents ?? []).map((doc) => (
        <li key={doc.id}>
          <span>{doc.original_filename}</span>
          <span className={`status-chip status-${doc.ingest_status}`}>{doc.ingest_status}</span>
          {doc.ingest_status === "failed" && (
            <button onClick={() => retry.mutate(doc.id)}>Retry</button>
          )}
        </li>
      ))}
    </ul>
  );
}
