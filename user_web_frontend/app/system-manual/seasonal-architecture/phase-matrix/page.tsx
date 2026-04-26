import MarkdownContent from "../../../../components/MarkdownContent";
import { getManualContent } from "../../../../lib/systemManualContent";

export default function PhaseMatrixPage() {
  const content = getManualContent("seasonal-architecture/phase-matrix");
  return (
    <>
      <h1 className="sm-page-title">2. THE PHASE MATRIX</h1>
      {content ? <MarkdownContent content={content} /> : <div className="sm-placeholder" />}
    </>
  );
}
