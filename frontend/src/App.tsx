import { useState } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ChatPane } from "./components/chat/ChatPane";
import { CourseSelector } from "./components/courses/CourseSelector";
import { DocumentList } from "./components/documents/DocumentList";
import { UploadDropzone } from "./components/documents/UploadDropzone";
import type { SourceTarget } from "./components/source-panel/SourcePanel";
import { SourcePanel } from "./components/source-panel/SourcePanel";
import type { RelatedFigure } from "./api/chat";

const queryClient = new QueryClient();

export default function App() {
  const [selectedCourseId, setSelectedCourseId] = useState<number | null>(null);
  const [sourceTarget, setSourceTarget] = useState<SourceTarget>(null);

  return (
    <QueryClientProvider client={queryClient}>
      <div className="app-shell">
        <aside className="app-sidebar">
          <div className="app-brand">
            <span className="app-brand-mark" aria-hidden="true">
              §
            </span>
            <h1 className="app-brand-name">Multi-Turn Hybrid Retrieval System with Agentic Memory</h1>
          </div>
          <CourseSelector selectedCourseId={selectedCourseId} onSelect={setSelectedCourseId} />
        </aside>
        <main className="app-main">
          {selectedCourseId !== null ? (
            <div className="app-workspace">
              <div className="app-column-documents">
                <UploadDropzone courseId={selectedCourseId} />
                <DocumentList courseId={selectedCourseId} />
              </div>
              <div className="app-column-chat">
                <ChatPane
                  key={selectedCourseId}
                  courseId={selectedCourseId}
                  onOpenSource={(chunkId) => setSourceTarget({ kind: "chunk", chunkId })}
                  onOpenFigure={(figure: RelatedFigure) =>
                    setSourceTarget({
                      kind: "figure",
                      documentId: figure.document_id,
                      pageNumber: figure.page_number,
                      filename: figure.filename,
                    })
                  }
                />
              </div>
            </div>
          ) : (
            <div className="app-empty-state">
              <h2>Pick a course to get started</h2>
              <p>Choose a course from the sidebar, or add a new one to upload your notes.</p>
            </div>
          )}
        </main>
        <SourcePanel target={sourceTarget} onClose={() => setSourceTarget(null)} />
      </div>
    </QueryClientProvider>
  );
}
