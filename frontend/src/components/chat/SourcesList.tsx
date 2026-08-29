import type { MessageSourceResponse } from "../../api/types";

export default function SourcesList({ sources }: { sources: MessageSourceResponse[] }) {
  if (sources.length === 0) return null;

  return (
    <div className="section">
      <h4>Sources</h4>
      <ol className="sources-list">
        {sources.map((source, i) => (
          <li key={source.knowledge_entry_id}>
            [{i + 1}] {source.title}
          </li>
        ))}
      </ol>
    </div>
  );
}
