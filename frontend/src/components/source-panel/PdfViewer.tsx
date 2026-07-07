import { useEffect, useRef } from "react";

import * as pdfjsLib from "pdfjs-dist";

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.mjs",
  import.meta.url
).toString();

interface PdfViewerProps {
  pdfUrl: string;
  pageNumber: number;
}

export function PdfViewer({ pdfUrl, pageNumber }: PdfViewerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let cancelled = false;

    async function render() {
      const loadingTask = pdfjsLib.getDocument(pdfUrl);
      const pdf = await loadingTask.promise;
      if (cancelled) return;

      const page = await pdf.getPage(pageNumber);
      const viewport = page.getViewport({ scale: 1.5 });

      const canvas = canvasRef.current;
      if (!canvas) return;
      canvas.width = viewport.width;
      canvas.height = viewport.height;

      const context = canvas.getContext("2d");
      if (!context) return;

      await page.render({ canvasContext: context, viewport }).promise;
    }

    render();
    return () => {
      cancelled = true;
    };
  }, [pdfUrl, pageNumber]);

  return <canvas ref={canvasRef} data-testid="pdf-canvas" />;
}
