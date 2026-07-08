import { useRef, useState } from "react";

import { useUploadDocuments } from "../../api/documents";
import "./UploadDropzone.css";

interface UploadDropzoneProps {
  courseId: number;
}

export function UploadDropzone({ courseId }: UploadDropzoneProps) {
  const upload = useUploadDocuments(courseId);
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);

  const handleFiles = (files: FileList | null) => {
    if (files && files.length > 0) {
      upload.mutate(files);
    }
  };

  return (
    <div
      className={`upload-dropzone${isDragging ? " is-dragging" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setIsDragging(true);
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setIsDragging(false);
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
      <span className="upload-dropzone-icon" aria-hidden="true">
        ⇪
      </span>
      <p>Drop PDF, DOCX, or PPTX files here, or click to select.</p>
      <p className="upload-dropzone-hint">PDF, DOCX, PPTX</p>
    </div>
  );
}
