import MarkdownContent from "../../../../components/MarkdownContent";
import { getManualContent } from "../../../../lib/systemManualContent";

export default function Phase4Page() {
  const content = getManualContent("telemetry-pipeline/phase-4-redemption-extraction");
  return (
    <>
      <h1 className="sm-page-title">PHASE 4: REDEMPTION EXTRACTION & NOISE FILTRATION</h1>
      {content ? <MarkdownContent content={content} /> : <div className="sm-placeholder" />}
    </>
  );
}
