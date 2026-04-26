import MarkdownContent from "../../components/MarkdownContent";
import { getManualContent } from "../../lib/systemManualContent";

export default function SystemManualOverviewPage() {
  const content = getManualContent("overview");
  return (
    <>
      <h1 className="sm-page-title">Overview</h1>
      {content ? <MarkdownContent content={content} /> : <div className="sm-placeholder" />}
    </>
  );
}
