import MarkdownContent from "../../../../components/MarkdownContent";
import { getManualContent } from "../../../../lib/systemManualContent";

export default function Phase3Page() {
  const content = getManualContent("telemetry-pipeline/phase-3-condition-parsing");
  return (
    <>
      <h1 className="sm-page-title">PHASE 3: CONDITION PARSING</h1>
      {content ? <MarkdownContent content={content} /> : <div className="sm-placeholder" />}
    </>
  );
}
