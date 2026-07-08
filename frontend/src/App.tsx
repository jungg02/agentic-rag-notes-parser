import { useState } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ChatPane } from "./components/chat/ChatPane";
import { CourseSelector } from "./components/courses/CourseSelector";
import { DocumentList } from "./components/documents/DocumentList";
import { UploadDropzone } from "./components/documents/UploadDropzone";
import { SourcePanel } from "./components/source-panel/SourcePanel";

const queryClient = new QueryClient();

export default function App() {
  const [selectedCourseId, setSelectedCourseId] = useState<number | null>(null);
  const [openChunkId, setOpenChunkId] = useState<number | null>(null);

  return (
    <QueryClientProvider client={queryClient}>
      <div className="app-shell">
        <aside className="app-sidebar">
          <div className="app-brand">
            <span className="app-brand-mark" aria-hidden="true">
              §
            </span>
            <h1 className="app-brand-name">Study Notes Parser</h1>
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
                <ChatPane courseId={selectedCourseId} onOpenSource={setOpenChunkId} />
              </div>
            </div>
          ) : (
            <div className="app-empty-state">
              <h2>Pick a course to get started</h2>
              <p>Choose a course from the sidebar, or add a new one to upload your notes.</p>
            </div>
          )}
        </main>
        <SourcePanel chunkId={openChunkId} onClose={() => setOpenChunkId(null)} />
      </div>
    </QueryClientProvider>
  );
}
