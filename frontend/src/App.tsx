import { useState } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CourseSelector } from "./components/courses/CourseSelector";
import { DocumentList } from "./components/documents/DocumentList";
import { UploadDropzone } from "./components/documents/UploadDropzone";

const queryClient = new QueryClient();

export default function App() {
  const [selectedCourseId, setSelectedCourseId] = useState<number | null>(null);

  return (
    <QueryClientProvider client={queryClient}>
      <div className="app-root">
        <h1>Study Notes Parser</h1>
        <CourseSelector selectedCourseId={selectedCourseId} onSelect={setSelectedCourseId} />
        {selectedCourseId !== null && (
          <>
            <UploadDropzone courseId={selectedCourseId} />
            <DocumentList courseId={selectedCourseId} />
          </>
        )}
      </div>
    </QueryClientProvider>
  );
}
