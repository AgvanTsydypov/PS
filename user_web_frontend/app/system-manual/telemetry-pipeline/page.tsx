import MarkdownContent from "../../../components/MarkdownContent";
import { getManualContent } from "../../../lib/systemManualContent";

export default function TelemetryPipelinePage() {
  const content = getManualContent("telemetry-pipeline/index");
  return (
    <>
      <h1 className="sm-page-title">TELEMETRY PIPELINE</h1>
      {content ? <MarkdownContent content={content} /> : <div className="sm-placeholder" />}
    </>
  );
}
