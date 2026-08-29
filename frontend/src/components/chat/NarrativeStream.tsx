import ReactMarkdown from "react-markdown";

interface NarrativeStreamProps {
  content: string;
  isStreaming: boolean;
}

export default function NarrativeStream({ content, isStreaming }: NarrativeStreamProps) {
  if (!content && !isStreaming) return null;

  return (
    <div className="narrative-block">
      <div className="narrative-markdown">
        <ReactMarkdown>{content}</ReactMarkdown>
      </div>
      {isStreaming && <span className="narrative-cursor">▋</span>}
    </div>
  );
}
