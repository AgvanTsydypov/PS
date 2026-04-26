import MarkdownContent from "../../../../components/MarkdownContent";
import { getManualContent } from "../../../../lib/systemManualContent";

export default function Phase6Page() {
  const content = getManualContent("telemetry-pipeline/phase-6-stellar-initialization");
  return (
    <>
      <h1 className="sm-page-title">PHASE 6: STELLAR INITIALIZATION</h1>
      {content ? <MarkdownContent content={content} /> : <div className="sm-placeholder" />}
    </>
  );
}
