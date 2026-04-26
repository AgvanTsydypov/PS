import MarkdownContent from "../../../../components/MarkdownContent";
import { getManualContent } from "../../../../lib/systemManualContent";

export default function Phase5Page() {
  const content = getManualContent("telemetry-pipeline/phase-5-behavioral-profiling");
  return (
    <>
      <h1 className="sm-page-title">PHASE 5: BEHAVIORAL PROFILING</h1>
      {content ? <MarkdownContent content={content} /> : <div className="sm-placeholder" />}
    </>
  );
}
