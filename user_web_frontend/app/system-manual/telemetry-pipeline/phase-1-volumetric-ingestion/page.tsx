import MarkdownContent from "../../../../components/MarkdownContent";
import { getManualContent } from "../../../../lib/systemManualContent";

export default function Phase1Page() {
  const content = getManualContent("telemetry-pipeline/phase-1-volumetric-ingestion");
  return (
    <>
      <h1 className="sm-page-title">PHASE 1: VOLUMETRIC INGESTION</h1>
      {content ? <MarkdownContent content={content} /> : <div className="sm-placeholder" />}
    </>
  );
}
