import ReactMarkdown from "react-markdown";
import type { MessageResponse } from "../../api/types";
import type { ApiBase } from "../../api/client";
import SourcesList from "./SourcesList";
import FeedbackButtons from "./FeedbackButtons";
import CopyButton from "./CopyButton";
import HiringSignalResultCard from "./HiringSignalResultCard";

interface HistoryMessageCardProps {
  message: MessageResponse;
  token: string;
  conversationId: string;
  /** True only for the most recent assistant message in the conversation
   * -- gates whether the "Generate Pitch" button can appear at all, so an
   * older strategy turn earlier in history never offers it (clicking it
   * would still act on the CURRENT latest context server-side, which
   * would be confusing this far back in the thread). */
  isLatest?: boolean;
  /** Present only when the parent wants to offer pitch generation here;
   * omitted entirely for contexts where it never applies. */
  onGeneratePitch?: () => void;
  /** Disables the Generate Pitch button while another exchange is in
   * flight, mirroring the composer's own disabled state. */
  generatePitchDisabled?: boolean;
  /** Which router this message's conversation lives under -- see ApiBase
   * in api/client.ts. Defaults to "chat" (SE) so existing callers are
   * unaffected; BD's screen passes "bd-chat". */
  basePath?: ApiBase;
}

/** Plain-text version of whatever this card actually renders, for
 * CopyButton -- mirrors the narrative/strategy/content branches below so
 * copying always matches what's on screen. */
function messageAsPlainText(message: MessageResponse): string {
  if (message.narrative) return message.narrative;
  if (message.strategy) {
    const s = message.strategy;
    return [
      `Situation summary: ${s.situation_summary}`,
      "",
      "Recommended strategy:",
      ...s.recommended_strategy.map((step, i) => `${i + 1}. ${step}`),
      "",
      `Next action: ${s.next_action}`,
    ].join("\n");
  }
  return message.content;
}

/**
 * Renders one assistant message -- both a reloaded message from GET
 * .../messages and a live turn once /strategy/stream's "result" event
 * arrives (see ChatPage.tsx), so the same rendering is used either way.
 * The streaming path only ever populates `narrative` (the JSON strategy
 * call is no longer made there); `strategy` is a fallback for any message
 * that does have it (e.g. via a future consumer of the non-streaming
 * /strategy endpoint).
 */
export default function HistoryMessageCard({
  message,
  token,
  conversationId,
  isLatest = false,
  onGeneratePitch,
  generatePitchDisabled = false,
  basePath = "chat",
}: HistoryMessageCardProps) {
  const { strategy, narrative, hiring_signal, situation_classification: sc } = message;

  if (hiring_signal) {
    return (
      <>
        <HiringSignalResultCard result={hiring_signal} />
        <div className="message-actions">
          <SourcesList sources={message.sources} />
          <FeedbackButtons token={token} conversationId={conversationId} messageId={message.id} basePath={basePath} />
        </div>
      </>
    );
  }

  return (
    <div className="turn-assistant">
      <CopyButton text={messageAsPlainText(message)} variant="icon" />

      {sc && (
        <div className="badge-row">
          <span className="badge">Stage: {sc.sales_stage}</span>
          <span className="badge">Problem: {sc.problem_type}</span>
          <span className="badge">Buyer: {sc.buyer_persona}</span>
        </div>
      )}

      {narrative ? (
        <div className="narrative-markdown">
          <ReactMarkdown>{narrative}</ReactMarkdown>
        </div>
      ) : strategy ? (
        <>
          <div className="section">
            <h4>Situation summary</h4>
            <p>{strategy.situation_summary}</p>
          </div>
          <div className="section">
            <h4>Recommended strategy</h4>
            <ul>
              {strategy.recommended_strategy.map((step, i) => (
                <li key={i}>{step}</li>
              ))}
            </ul>
          </div>
          <div className="section">
            <h4>Next action</h4>
            <div className="next-action">{strategy.next_action}</div>
          </div>
        </>
      ) : (
        <div className="narrative-markdown">
          <ReactMarkdown>{message.content}</ReactMarkdown>
        </div>
      )}

      <div className="message-actions">
        <CopyButton text={messageAsPlainText(message)} />
        {isLatest && message.show_pitch_button && onGeneratePitch && (
          <button
            className="generate-pitch-btn"
            onClick={onGeneratePitch}
            disabled={generatePitchDisabled}
            type="button"
          >
            🎯 Generate Pitch
          </button>
        )}
      </div>

      <SourcesList sources={message.sources} />
      <FeedbackButtons
        token={token}
        conversationId={conversationId}
        messageId={message.id}
        basePath={basePath}
      />
    </div>
  );
}
