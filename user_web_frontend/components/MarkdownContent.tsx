import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

function normalizeArrows(text: string): string {
  return text.replace(/—+>/g, "⟶");
}

export default function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="sm-md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{normalizeArrows(content)}</ReactMarkdown>
    </div>
  );
}
