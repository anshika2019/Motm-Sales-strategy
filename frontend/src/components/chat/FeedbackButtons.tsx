import { useState } from "react";
import { submitFeedback, type ApiBase } from "../../api/client";
import type { FeedbackRating } from "../../api/types";

interface FeedbackButtonsProps {
  token: string;
  conversationId: string;
  messageId: string;
  /** Which router this message's conversation lives under -- see
   * ApiBase in api/client.ts. Defaults to "chat" (SE) so existing callers
   * are unaffected; BD's screens pass "bd-chat". */
  basePath?: ApiBase;
}

export default function FeedbackButtons({
  token,
  conversationId,
  messageId,
  basePath = "chat",
}: FeedbackButtonsProps) {
  const [rating, setRating] = useState<FeedbackRating | null>(null);
  const [showText, setShowText] = useState(false);
  const [feedbackText, setFeedbackText] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const handleRate = (r: FeedbackRating) => {
    setRating(r);
    setShowText(true);
    setSubmitted(false);
  };

  const handleSubmit = async () => {
    if (!rating || submitting) return;
    setSubmitting(true);
    try {
      await submitFeedback(token, conversationId, messageId, rating, feedbackText || null, basePath);
      setSubmitted(true);
    } finally {
      setSubmitting(false);
    }
  };

  if (submitted) {
    return (
      <div className="feedback-bar submitted">
        <span>✓ Feedback recorded — this will inform later replies in this conversation.</span>
      </div>
    );
  }

  return (
    <div className="feedback-bar">
      <span className="feedback-label">Was this helpful?</span>
      <button
        className={`feedback-btn ${rating === "useful" ? "active-positive" : ""}`}
        onClick={() => handleRate("useful")}
        title="Useful"
        type="button"
      >
        👍
      </button>
      <button
        className={`feedback-btn ${rating === "not_useful" ? "active-negative" : ""}`}
        onClick={() => handleRate("not_useful")}
        title="Not useful"
        type="button"
      >
        👎
      </button>
      {showText && (
        <div className="feedback-text-row">
          <input
            type="text"
            placeholder="Optional: what was wrong or missing?"
            value={feedbackText}
            onChange={(e) => setFeedbackText(e.target.value)}
            className="feedback-input"
          />
          <button className="feedback-submit" onClick={handleSubmit} disabled={submitting} type="button">
            Send
          </button>
        </div>
      )}
    </div>
  );
}
