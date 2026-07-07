import { useState } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ChatPane } from "./components/chat/ChatPane";
import { CourseSelector } from "./components/courses/CourseSelector";
import { DocumentList } from "./components/documents/DocumentList";
import { UploadDropzone } from "./components/documents/UploadDropzone";

const queryClient = new QueryClient();

export default function App() {
  const [selectedCourseId, setSelectedCourseId] = useState<number | null>(null);
  const [openChunkId, setOpenChunkId] = useState<number | null>(null);

  return (
    <QueryClientProvider client={queryClient}>
      <div className="app-root">
        <h1>Study Notes Parser</h1>
        <CourseSelector selectedCourseId={selectedCourseId} onSelect={setSelectedCourseId} />
        {selectedCourseId !== null && (
          <>
            <UploadDropzone courseId={selectedCourseId} />
            <DocumentList courseId={selectedCourseId} />
            <ChatPane courseId={selectedCourseId} onOpenSource={setOpenChunkId} />
          </>
        )}
        {openChunkId !== null && <div data-testid="source-panel-placeholder">chunk {openChunkId}</div>}
      </div>
    </QueryClientProvider>
  );
}
