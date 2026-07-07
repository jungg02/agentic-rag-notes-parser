import { useRef } from "react";

import { useUploadDocuments } from "../../api/documents";

interface UploadDropzoneProps {
  courseId: number;
}

export function UploadDropzone({ courseId }: UploadDropzoneProps) {
  const upload = useUploadDocuments(courseId);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFiles = (files: FileList | null) => {
    if (files && files.length > 0) {
      upload.mutate(files);
    }
  };

  return (
    <div
      className="upload-dropzone"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        handleFiles(e.dataTransfer.files);
      }}
      onClick={() => inputRef.current?.click()}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        accept=".pdf,.docx,.pptx"
        aria-label="Upload notes"
        style={{ display: "none" }}
        onChange={(e) => handleFiles(e.target.files)}
      />
      <p>Drop PDF, DOCX, or PPTX files here, or click to select.</p>
    </div>
  );
}
