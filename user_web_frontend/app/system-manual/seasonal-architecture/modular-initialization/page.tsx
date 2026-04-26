import MarkdownContent from "../../../../components/MarkdownContent";
import { getManualContent } from "../../../../lib/systemManualContent";

export default function ModularInitializationPage() {
  const content = getManualContent("seasonal-architecture/modular-initialization");
  return (
    <>
      <h1 className="sm-page-title">3. THE MODULAR INITIALIZATION PROTOCOL</h1>
      {content ? <MarkdownContent content={content} /> : <div className="sm-placeholder" />}
    </>
  );
}
