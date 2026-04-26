import MarkdownContent from "../../../components/MarkdownContent";
import { getManualContent } from "../../../lib/systemManualContent";

export default function SeasonalArchitecturePage() {
  const content = getManualContent("seasonal-architecture/index");
  return (
    <>
      <h1 className="sm-page-title">SEASONAL ARCHITECTURE</h1>
      {content ? <MarkdownContent content={content} /> : <div className="sm-placeholder" />}
    </>
  );
}
