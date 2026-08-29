import type { ApiError } from "../../api/types";

interface ChatErrorNoticeProps {
  error: ApiError;
  onRetry?: () => void;
}

export default function ChatErrorNotice({ error, onRetry }: ChatErrorNoticeProps) {
  if (error.kind === "quality_gate") {
    return (
      <div className="turn-assistant quality-gate">
        <div className="section" style={{ marginBottom: error.detail.missing.length ? 12 : 0 }}>
          <h4>Need a bit more detail</h4>
          <p>{error.detail.message}</p>
        </div>
        {error.detail.missing.length > 0 && (
          <ul>
            {error.detail.missing.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  if (error.kind === "not_found") {
    return (
      <div className="turn-assistant notice">
        <p style={{ margin: 0 }}>
          That conversation could no longer be found, so a new one was started. Please try
          sending your request again.
        </p>
      </div>
    );
  }

  const message =
    error.kind === "network"
      ? error.message
      : error.kind === "server"
        ? "Something went wrong generating the strategy."
        : error.kind === "validation"
          ? "Please check your inputs and try again."
          : "message" in error
            ? error.message
            : "Something went wrong.";

  return (
    <div className="turn-assistant notice">
      <p style={{ margin: 0 }}>{message}</p>
      {onRetry && (
        <button className="retry-button" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
