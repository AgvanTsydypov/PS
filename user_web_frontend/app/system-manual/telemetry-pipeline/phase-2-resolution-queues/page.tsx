import MarkdownContent from "../../../../components/MarkdownContent";
import { getManualContent } from "../../../../lib/systemManualContent";

export default function Phase2Page() {
  const content = getManualContent("telemetry-pipeline/phase-2-resolution-queues");
  return (
    <>
      <h1 className="sm-page-title">PHASE 2: THE RESOLUTION QUEUES</h1>
      {content ? <MarkdownContent content={content} /> : <div className="sm-placeholder" />}
    </>
  );
}
