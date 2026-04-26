import MarkdownContent from "../../../../components/MarkdownContent";
import { getManualContent } from "../../../../lib/systemManualContent";

export default function ChronologicalGridPage() {
  const content = getManualContent("seasonal-architecture/chronological-grid");
  return (
    <>
      <h1 className="sm-page-title">1. THE CHRONOLOGICAL GRID</h1>
      {content ? <MarkdownContent content={content} /> : <div className="sm-placeholder" />}
    </>
  );
}
