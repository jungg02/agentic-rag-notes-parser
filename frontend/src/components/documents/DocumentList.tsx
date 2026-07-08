import { useDocuments, useRetryDocument } from "../../api/documents";
import "./DocumentList.css";

interface DocumentListProps {
  courseId: number;
}

export function DocumentList({ courseId }: DocumentListProps) {
  const { data: documents, isLoading } = useDocuments(courseId);
  const retry = useRetryDocument(courseId);

  return (
    <div className="document-list">
      <h2 className="panel-heading">Documents</h2>
      {isLoading ? (
        <p className="document-list-status">Loading documents...</p>
      ) : (documents ?? []).length === 0 ? (
        <p className="document-list-empty">No documents yet. Upload your notes above to get started.</p>
      ) : (
        <ul className="document-items">
          {(documents ?? []).map((doc) => (
            <li key={doc.id} className="document-item">
              <span className="document-name">{doc.original_filename}</span>
              <span className={`status-chip status-${doc.ingest_status}`}>{doc.ingest_status}</span>
              {doc.ingest_status === "failed" && (
                <button className="document-retry" onClick={() => retry.mutate(doc.id)}>
                  Retry
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
