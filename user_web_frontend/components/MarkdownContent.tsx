import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

import ZoomableImage from "./ZoomableImage";

function normalizeArrows(text: string): string {
  return text.replace(/—+>/g, "⟶");
}

export default function MarkdownContent({ content }: { content: string }) {
  return (
    <div className="sm-md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{ img: (props) => <ZoomableImage {...props} /> }}
      >
        {normalizeArrows(content)}
      </ReactMarkdown>
    </div>
  );
}
