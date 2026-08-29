import { useCallback, useRef, useState } from "react";
import {
  ApiException,
  createConversation,
  getConversationMessages,
  postStrategyStream,
} from "../api/client";
import type { ApiError, ConversationSummary, MeResponse, MessageResponse, StrategyRequest } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import ChatComposer, { type StrategyFormValues } from "../components/chat/ChatComposer";
import UserTurnCard from "../components/chat/UserTurnCard";
import ChatErrorNotice from "../components/chat/ChatErrorNotice";
import LoadingBubble from "../components/chat/LoadingBubble";
import NarrativeStream from "../components/chat/NarrativeStream";
import HistoryPanel from "../components/chat/HistoryPanel";
import HistoryMessageCard from "../components/chat/HistoryMessageCard";
import ThemeToggle from "../components/ThemeToggle";
import { getGenerationLabel } from "../components/chat/generationLabel";
import { SendIcon } from "../components/chat/icons";

// Prefers the profile's first name; falls back to a cleaned-up version of
// the email's local part (stripped of digits/separators and title-cased)
// for accounts that never set a full_name.
function getDisplayName(user: MeResponse | null): string {
  if (user?.full_name) return user.full_name.trim().split(/\s+/)[0];
  if (user?.email) {
    const localPart = user.email.split("@")[0];
    const words = localPart
      .split(/[._-]+/)
      .map((w) => w.replace(/\d+$/, ""))
      .filter(Boolean);
    if (words.length > 0) {
      return words.map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
    }
  }
  return "there";
}

// Display-only stand-in for the pitch-button trigger's UserTurnCard --
// the request itself carries no situation/website/product (see `trigger`
// on Exchange below), but the card still needs something to show as
// "You asked".
const PITCH_TRIGGER_DISPLAY_VALUES: StrategyFormValues = {
  situation: "",
  website_url: "",
  product: "",
  raw_message: "Generate a full sales pitch",
};

/** True when the user's own text already explicitly asked for a pitch --
 * either via the dedicated pitch-trigger button or by typing the word
 * "pitch" directly. Used to hide the "Generate Pitch" button after such a
 * turn even if the backend's show_pitch_button still says yes. */
function wasExplicitPitchRequest(trigger: string | undefined, text: string): boolean {
  return trigger === "generate_pitch" || /\bpitch\b/i.test(text);
}

/** Reloaded history messages store the user's turn as the backend's raw
 * "Website: ...\nProduct: ...\nSituation: ..." template (see
 * app/routers/chat.py) rather than the structured form values a live
 * submission carries. Parse it back into StrategyFormValues so reloaded
 * turns render through UserTurnCard exactly like live ones -- otherwise
 * blank website/product lines show up as bare "Website:"/"Product:" labels. */
function parseStoredUserContent(content: string): StrategyFormValues {
  const match = content.match(/^Website:\s*(.*)\nProduct:\s*(.*)\nSituation:\s*([\s\S]*)$/);
  if (!match) {
    return { situation: content, website_url: "", product: "", raw_message: content };
  }
  const [, website, product, situation] = match;
  return {
    situation,
    website_url: website.trim(),
    product: product.trim(),
    raw_message: situation,
  };
}

interface Exchange {
  id: string;
  values: StrategyFormValues;
  /** Set to "generate_pitch" for a pitch-button-triggered exchange -- see
   * PITCH_TRIGGER_DISPLAY_VALUES above and handleGeneratePitch below.
   * Undefined for a normal composer submission. */
  trigger?: string;
  state: "loading" | "success" | "error";
  loadingLabel: string;
  narrativeText: string;
  isStreamingNarrative: boolean;
  result?: MessageResponse;
  error?: ApiError;
}

export default function ChatPage() {
  const { token, user, logout, handleUnauthorized } = useAuth();
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [historyMessages, setHistoryMessages] = useState<MessageResponse[]>([]);
  const [websiteFieldError, setWebsiteFieldError] = useState<string | null>(null);
  const [isComposerOpen, setIsComposerOpen] = useState(true);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [historyRefreshSignal, setHistoryRefreshSignal] = useState(0);
  const conversationIdRef = useRef<string | null>(null);
  const isBusy = exchanges.some((e) => e.state === "loading");

  const ensureConversation = useCallback(async (): Promise<string> => {
    if (conversationIdRef.current) return conversationIdRef.current;
    const conversation = await createConversation(token!);
    conversationIdRef.current = conversation.id;
    setActiveConversationId(conversation.id);
    return conversation.id;
  }, [token]);

  const runExchange = useCallback(
    async (exchange: Exchange) => {
      setWebsiteFieldError(null);
      const exchangeId = exchange.id;
      // A pitch-button trigger carries no situation/website/product --
      // the backend resolves it against the last strategy turn's stored
      // context instead (see _handle_pitch_trigger in app/routers/chat.py).
      const requestBody: StrategyRequest = exchange.trigger
        ? { trigger: exchange.trigger }
        : {
            situation: exchange.values.situation || undefined,
            website_url: exchange.values.website_url || undefined,
            product: exchange.values.product || undefined,
            raw_message: exchange.values.raw_message || undefined,
          };
      try {
        const conversationId = await ensureConversation();
        await postStrategyStream(
          token!,
          conversationId,
          requestBody,
          {
            onNarrativeChunk: (delta) => {
              setExchanges((prev) =>
                prev.map((e) =>
                  e.id === exchangeId
                    ? { ...e, narrativeText: e.narrativeText + delta, isStreamingNarrative: true }
                    : e,
                ),
              );
            },
            onResult: (result) => {
              setExchanges((prev) =>
                prev.map((e) =>
                  e.id === exchangeId
                    ? { ...e, state: "success", result, isStreamingNarrative: false }
                    : e,
                ),
              );
              setIsComposerOpen(false);
              setHistoryRefreshSignal((n) => n + 1);
            },
            onError: (message) => {
              setExchanges((prev) =>
                prev.map((e) =>
                  e.id === exchangeId
                    ? { ...e, state: "error", error: { kind: "server", message }, isStreamingNarrative: false }
                    : e,
                ),
              );
            },
          },
        );
      } catch (err) {
        const error: ApiError =
          err instanceof ApiException ? err.error : { kind: "network", message: "Something went wrong." };

        if (error.kind === "unauthorized") {
          handleUnauthorized();
          return;
        }
        if (error.kind === "forbidden") {
          handleUnauthorized();
          return;
        }
        if (error.kind === "not_found") {
          // The conversation is gone server-side -- drop it so the next
          // submit transparently starts a fresh one.
          conversationIdRef.current = null;
          setActiveConversationId(null);
        }
        if (error.kind === "bad_url") {
          setWebsiteFieldError(error.message);
        }

        setExchanges((prev) => prev.map((e) => (e.id === exchangeId ? { ...e, state: "error", error } : e)));
      }
    },
    [ensureConversation, token, handleUnauthorized],
  );

  const handleSubmit = useCallback(
    (values: StrategyFormValues) => {
      const id = crypto.randomUUID();
      const exchange: Exchange = {
        id,
        values,
        state: "loading",
        loadingLabel: getGenerationLabel(values.raw_message),
        narrativeText: "",
        isStreamingNarrative: false,
      };
      setExchanges((prev) => [...prev, exchange]);
      void runExchange(exchange);
    },
    [runExchange],
  );

  // Triggered by the "Generate Pitch" button on the latest strategy turn
  // (see HistoryMessageCard's isLatest/onGeneratePitch props below). Skips
  // the composer entirely -- the backend generates the pitch from the
  // conversation's last strategy context.
  const handleGeneratePitch = useCallback(() => {
    const id = crypto.randomUUID();
    const exchange: Exchange = {
      id,
      values: PITCH_TRIGGER_DISPLAY_VALUES,
      trigger: "generate_pitch",
      state: "loading",
      loadingLabel: getGenerationLabel(PITCH_TRIGGER_DISPLAY_VALUES.raw_message, "generate_pitch"),
      narrativeText: "",
      isStreamingNarrative: false,
    };
    setExchanges((prev) => [...prev, exchange]);
    setIsComposerOpen(false);
    void runExchange(exchange);
  }, [runExchange]);

  const handleRetry = useCallback(
    (exchange: Exchange) => {
      setExchanges((prev) =>
        prev.map((e) =>
          e.id === exchange.id
            ? { ...e, state: "loading", error: undefined, narrativeText: "", isStreamingNarrative: false }
            : e,
        ),
      );
      void runExchange(exchange);
    },
    [runExchange],
  );

  const handleSelectConversation = useCallback(
    async (conversation: ConversationSummary) => {
      conversationIdRef.current = conversation.id;
      setActiveConversationId(conversation.id);
      setExchanges([]);
      setIsComposerOpen(true);
      try {
        const result = await getConversationMessages(token!, conversation.id);
        setHistoryMessages(result.messages);
      } catch {
        setHistoryMessages([]);
      }
    },
    [token],
  );

  const handleNewConversation = useCallback(() => {
    conversationIdRef.current = null;
    setActiveConversationId(null);
    setExchanges([]);
    setHistoryMessages([]);
    setIsComposerOpen(true);
  }, []);

  const handleConversationDeleted = useCallback(
    (conversationId: string) => {
      if (conversationId === conversationIdRef.current) {
        handleNewConversation();
      }
    },
    [handleNewConversation],
  );

  const isEmpty = exchanges.length === 0 && historyMessages.length === 0;

  return (
    <div className="app-shell">
      <input type="checkbox" id="sidebar-toggle" className="sidebar-toggle-checkbox" />
      <div className="top-bar">
        <div className="top-bar-left">
          <label htmlFor="sidebar-toggle" className="sidebar-toggle-button" aria-label="Toggle conversation list">
            ☰
          </label>
          <span className="top-bar-title">MOTM Sales Director</span>
        </div>
        <div className="top-bar-user">
          <span>{user?.email}</span>
          <button className="link-button" onClick={() => logout()}>
            Sign out
          </button>
          <ThemeToggle />
        </div>
      </div>

      <div className="chat-layout">
        <HistoryPanel
          token={token!}
          activeConversationId={activeConversationId}
          onSelectConversation={handleSelectConversation}
          onNewConversation={handleNewConversation}
          onConversationDeleted={handleConversationDeleted}
          refreshSignal={historyRefreshSignal}
        />

        <div className="chat-main">
          <div className="chat-body">
            {isEmpty && (
              <div className="empty-state">
                <p className="empty-state-greeting">Hi {getDisplayName(user)}</p>
                <h2>Describe your sales situation</h2>
                <p>
                  Tell us what's happening with a prospect. Add their website and what you're
                  selling if you have them — it sharpens the strategy — but you can start with just
                  the situation.
                </p>
              </div>
            )}

            {historyMessages.map((message, index) => {
              if (message.sender === "user") {
                return <UserTurnCard key={message.id} values={parseStoredUserContent(message.content)} />;
              }
              const precedingUser = historyMessages[index - 1];
              const explicitPitch =
                precedingUser?.sender === "user" &&
                wasExplicitPitchRequest(undefined, parseStoredUserContent(precedingUser.content).situation);
              return (
                <HistoryMessageCard
                  key={message.id}
                  message={message}
                  token={token!}
                  conversationId={activeConversationId!}
                  // Only the very last message overall can offer
                  // "Generate Pitch" -- if there's a live exchange after
                  // history, that exchange's own result takes over this
                  // role instead (see the isLatest check below).
                  isLatest={exchanges.length === 0 && index === historyMessages.length - 1}
                  onGeneratePitch={explicitPitch ? undefined : handleGeneratePitch}
                  generatePitchDisabled={isBusy}
                />
              );
            })}

            {exchanges.map((exchange, index) => (
              <div key={exchange.id} style={{ display: "contents" }}>
                <UserTurnCard values={exchange.values} />
                {exchange.state === "loading" && !exchange.narrativeText && (
                  <LoadingBubble label={exchange.loadingLabel} />
                )}
                {exchange.state !== "success" && (exchange.narrativeText || exchange.isStreamingNarrative) && (
                  <NarrativeStream content={exchange.narrativeText} isStreaming={exchange.isStreamingNarrative} />
                )}
                {exchange.state === "success" && exchange.result && (
                  <HistoryMessageCard
                    message={exchange.result}
                    token={token!}
                    conversationId={activeConversationId!}
                    isLatest={index === exchanges.length - 1}
                    onGeneratePitch={
                      wasExplicitPitchRequest(exchange.trigger, exchange.values.raw_message || exchange.values.situation)
                        ? undefined
                        : handleGeneratePitch
                    }
                    generatePitchDisabled={isBusy}
                  />
                )}
                {exchange.state === "error" && exchange.error && exchange.error.kind !== "bad_url" && (
                  <ChatErrorNotice error={exchange.error} onRetry={() => handleRetry(exchange)} />
                )}
              </div>
            ))}
          </div>

          <div className="composer">
            <div className="composer-inner">
              {isComposerOpen ? (
                <ChatComposer
                  onSubmit={handleSubmit}
                  disabled={isBusy}
                  websiteFieldError={websiteFieldError}
                />
              ) : (
                <button className="composer-collapsed-button" onClick={() => setIsComposerOpen(true)}>
                  <span className="composer-collapsed-placeholder">Ask another question…</span>
                  <span className="composer-collapsed-send">
                    <SendIcon />
                  </span>
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
