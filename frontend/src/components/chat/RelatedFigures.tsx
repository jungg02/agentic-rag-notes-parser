import type { RelatedFigure } from "../../api/chat";
import "./RelatedFigures.css";

interface RelatedFiguresProps {
  figures: RelatedFigure[] | undefined;
  onOpenFigure: (figure: RelatedFigure) => void;
}

export function RelatedFigures({ figures, onOpenFigure }: RelatedFiguresProps) {
  if (!figures || figures.length === 0) return null;

  return (
    <div className="related-figures">
      {figures.map((figure) => (
        <button
          key={figure.figure_id}
          className="related-figure-thumb"
          title={`${figure.filename}, page ${figure.page_number}`}
          onClick={() => onOpenFigure(figure)}
        >
          <img src={`/api/figures/${figure.figure_id}/image`} alt={`Figure from ${figure.filename}, page ${figure.page_number}`} />
          <span className="related-figure-caption">
            {figure.filename} — p.{figure.page_number}
          </span>
        </button>
      ))}
    </div>
  );
}
