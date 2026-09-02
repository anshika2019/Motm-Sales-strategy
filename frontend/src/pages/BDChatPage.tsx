import { useCallback, useRef, useState } from "react";
import {
  ApiException,
  createConversation,
  getConversationMessages,
  postBDHiringSignalOutreach,
  postStrategyStream,
} from "../api/client";
import type {
  ApiError,
  BDHiringSignalRequest,
  BDHiringSignalResponse,
  BDStrategyRequest,
  ConversationSummary,
  MeResponse,
  MessageResponse,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";
import BDComposer, { type BDStrategyFormValues } from "../components/chat/BDComposer";
import BDUserTurnCard from "../components/chat/BDUserTurnCard";
import HiringSignalComposer from "../components/chat/HiringSignalComposer";
import HiringSignalResultCard from "../components/chat/HiringSignalResultCard";
import HiringSignalUserTurnCard from "../components/chat/HiringSignalUserTurnCard";
import ChatErrorNotice from "../components/chat/ChatErrorNotice";
import LoadingBubble from "../components/chat/LoadingBubble";
import NarrativeStream from "../components/chat/NarrativeStream";
import HistoryPanel from "../components/chat/HistoryPanel";
import HistoryMessageCard from "../components/chat/HistoryMessageCard";
import ThemeToggle from "../components/ThemeToggle";
import { getGenerationLabel } from "../components/chat/generationLabel";
import { SendIcon, SidebarIcon, XIcon } from "../components/chat/icons";

// BD counterpart of ChatPage.tsx (SE) -- mirrors its structure closely,
// swapping in BD's composer/turn-card and pointing every API call at
// "bd-chat" instead of the default "chat" base (see ApiBase in
// api/client.ts). See getDisplayName's docstring there for why this copy
// lives here too rather than being imported -- kept identical on purpose,
// small enough that a shared util would be more indirection than reuse.
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

const PITCH_TRIGGER_DISPLAY_VALUES: BDStrategyFormValues = {
  situation: "",
  raw_message: "Generate a full sales pitch",
  prospect_company: "",
  prospect_website: "",
  contact_designation: "",
  opportunity_stage: "",
  additional_context: "",
};

function wasExplicitPitchRequest(trigger: string | undefined, text: string): boolean {
  return trigger === "generate_pitch" || /\bpitch\b/i.test(text);
}

/** Reloaded history messages store the user's turn as the BD router's raw
 * "Prospect: ...\nWebsite: ...\nSituation: ..." template (see
 * app/routers/bd_chat.py) -- parse it back into BDStrategyFormValues so
 * reloaded turns render through BDUserTurnCard exactly like live ones. */
function parseStoredUserContent(content: string): BDStrategyFormValues {
  const match = content.match(/^Prospect:\s*(.*)\nWebsite:\s*(.*)\nSituation:\s*([\s\S]*)$/);
  if (!match) {
    return {
      situation: content,
      raw_message: content,
      prospect_company: "",
      prospect_website: "",
      contact_designation: "",
      opportunity_stage: "",
      additional_context: "",
    };
  }
  const [, prospectCompany, prospectWebsite, situation] = match;
  return {
    situation,
    raw_message: situation,
    prospect_company: prospectCompany.trim(),
    prospect_website: prospectWebsite.trim(),
    contact_designation: "",
    opportunity_stage: "",
    additional_context: "",
  };
}

/** Reloaded hiring-signal user turns are stored as the router's raw
 * "Company: ...\nWebsite: ...\nHiring role: ...\nJob post: ..." template
 * (see post_bd_hiring_signal_outreach in app/routers/bd_chat.py). */
function parseStoredHiringSignalContent(content: string): BDHiringSignalRequest {
  const match = content.match(
    /^Company:\s*(.*)\nWebsite:\s*(.*)\nHiring role:\s*(.*)\nJob post:\s*([\s\S]*)$/,
  );
  if (!match) return { company_name: content };
  const [, companyName, companyWebsite, hiringRole, jobPostText] = match;
  return {
    company_name: companyName.trim() || undefined,
    company_website: companyWebsite.trim() || undefined,
    hiring_role: hiringRole.trim() || undefined,
    job_post_text: jobPostText.trim() || undefined,
  };
}

interface BaseExchange {
  id: string;
  state: "loading" | "success" | "error";
  error?: ApiError;
}

interface SituationExchange extends BaseExchange {
  kind: "situation";
  values: BDStrategyFormValues;
  trigger?: string;
  loadingLabel: string;
  narrativeText: string;
  isStreamingNarrative: boolean;
  result?: MessageResponse;
  // Which composer mode this was submitted from -- captured at submit
  // time from composerMode below. Gates the "Generate Pitch" button
  // together with show_pitch_button: the backend sets show_pitch_button
  // on every completed strategy turn regardless of how casual the message
  // was (see _build_message_response's default in app/routers/chat.py),
  // so without this every plain ad-hoc question would also offer to
  // generate a pitch. Only a turn submitted through the explicit "Describe
  // a situation" mode is eligible.
  submissionMode: "plain" | "situation";
}

interface HiringSignalExchange extends BaseExchange {
  kind: "hiring_signal";
  values: BDHiringSignalRequest;
  result?: BDHiringSignalResponse;
}

type Exchange = SituationExchange | HiringSignalExchange;

interface BDChatPageProps {
  // Present only for accounts that also hold the admin role -- lets a
  // dual-role (e.g. admin + BD) user reach the admin dashboard without
  // affecting anyone else's routing. See App.tsx.
  onOpenAdmin?: () => void;
  onOpenSettings: () => void;
}

// Sidebar defaults open on desktop but starts closed on a narrow/mobile
// viewport, matching the drawer's prior default-hidden behavior there
// (see the <=780px rules in styles.css) while adding a real open/close
// toggle for desktop, which previously had none.
function defaultSidebarOpen(): boolean {
  return typeof window === "undefined" || window.innerWidth > 780;
}

export default function BDChatPage({ onOpenAdmin, onOpenSettings }: BDChatPageProps) {
  const { token, user, logout, handleUnauthorized } = useAuth();
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [historyMessages, setHistoryMessages] = useState<MessageResponse[]>([]);
  const [isComposerOpen, setIsComposerOpen] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(defaultSidebarOpen);
  // "plain" is the default, unselected state -- a bare chat textarea with
  // neither tab active (like Claude/ChatGPT's plain composer). Clicking
  // either tab switches into that mode's extra fields; the tab's own close
  // (x) switches back to "plain" rather than there being no way back.
  const [composerMode, setComposerMode] = useState<"plain" | "situation" | "hiring_signal">("plain");
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [historyRefreshSignal, setHistoryRefreshSignal] = useState(0);
  const conversationIdRef = useRef<string | null>(null);
  const isBusy = exchanges.some((e) => e.state === "loading");

  const ensureConversation = useCallback(async (): Promise<string> => {
    if (conversationIdRef.current) return conversationIdRef.current;
    const conversation = await createConversation(token!, "bd-chat");
    conversationIdRef.current = conversation.id;
    setActiveConversationId(conversation.id);
    return conversation.id;
  }, [token]);

  const runExchange = useCallback(
    async (exchange: SituationExchange) => {
      const exchangeId = exchange.id;
      const requestBody: BDStrategyRequest = exchange.trigger
        ? { trigger: exchange.trigger }
        : {
            situation: exchange.values.situation || undefined,
            raw_message: exchange.values.raw_message || undefined,
            prospect_company: exchange.values.prospect_company || undefined,
            prospect_website: exchange.values.prospect_website || undefined,
            contact_designation: exchange.values.contact_designation || undefined,
            opportunity_stage: exchange.values.opportunity_stage || undefined,
            additional_context: exchange.values.additional_context || undefined,
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
                  e.id === exchangeId && e.kind === "situation"
                    ? { ...e, narrativeText: e.narrativeText + delta, isStreamingNarrative: true }
                    : e,
                ),
              );
            },
            onResult: (result) => {
              setExchanges((prev) =>
                prev.map((e) =>
                  e.id === exchangeId && e.kind === "situation"
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
                  e.id === exchangeId && e.kind === "situation"
                    ? { ...e, state: "error", error: { kind: "server", message }, isStreamingNarrative: false }
                    : e,
                ),
              );
            },
          },
          "bd-chat",
        );
      } catch (err) {
        const error: ApiError =
          err instanceof ApiException ? err.error : { kind: "network", message: "Something went wrong." };

        if (error.kind === "unauthorized" || error.kind === "forbidden") {
          handleUnauthorized();
          return;
        }
        if (error.kind === "not_found") {
          conversationIdRef.current = null;
          setActiveConversationId(null);
        }

        setExchanges((prev) => prev.map((e) => (e.id === exchangeId ? { ...e, state: "error", error } : e)));
      }
    },
    [ensureConversation, token, handleUnauthorized],
  );

  const runHiringSignalExchange = useCallback(
    async (exchange: HiringSignalExchange) => {
      const exchangeId = exchange.id;
      try {
        const conversationId = await ensureConversation();
        const result = await postBDHiringSignalOutreach(token!, conversationId, exchange.values);
        setExchanges((prev) =>
          prev.map((e) =>
            e.id === exchangeId && e.kind === "hiring_signal" ? { ...e, state: "success", result } : e,
          ),
        );
        setIsComposerOpen(false);
        setHistoryRefreshSignal((n) => n + 1);
      } catch (err) {
        const error: ApiError =
          err instanceof ApiException ? err.error : { kind: "network", message: "Something went wrong." };

        if (error.kind === "unauthorized" || error.kind === "forbidden") {
          handleUnauthorized();
          return;
        }
        if (error.kind === "not_found") {
          conversationIdRef.current = null;
          setActiveConversationId(null);
        }

        setExchanges((prev) => prev.map((e) => (e.id === exchangeId ? { ...e, state: "error", error } : e)));
      }
    },
    [ensureConversation, token, handleUnauthorized],
  );

  const handleSubmit = useCallback(
    (values: BDStrategyFormValues) => {
      const id = crypto.randomUUID();
      const exchange: SituationExchange = {
        id,
        kind: "situation",
        values,
        state: "loading",
        loadingLabel: getGenerationLabel(values.raw_message),
        narrativeText: "",
        isStreamingNarrative: false,
        submissionMode: composerMode === "situation" ? "situation" : "plain",
      };
      setExchanges((prev) => [...prev, exchange]);
      void runExchange(exchange);
    },
    [runExchange, composerMode],
  );

  const handleSubmitHiringSignal = useCallback(
    (values: BDHiringSignalRequest) => {
      const id = crypto.randomUUID();
      const exchange: HiringSignalExchange = { id, kind: "hiring_signal", values, state: "loading" };
      setExchanges((prev) => [...prev, exchange]);
      // Collapse immediately (not just on success) -- the form is tall and
      // the two-stage generation takes several seconds, so leaving it open
      // during loading buries the loading indicator/eventual result below
      // a wall of empty fields. Mirrors handleGeneratePitch's same call.
      setIsComposerOpen(false);
      void runHiringSignalExchange(exchange);
    },
    [runHiringSignalExchange],
  );

  const handleGeneratePitch = useCallback(() => {
    const id = crypto.randomUUID();
    const exchange: SituationExchange = {
      id,
      kind: "situation",
      values: PITCH_TRIGGER_DISPLAY_VALUES,
      trigger: "generate_pitch",
      state: "loading",
      loadingLabel: getGenerationLabel(PITCH_TRIGGER_DISPLAY_VALUES.raw_message, "generate_pitch"),
      narrativeText: "",
      isStreamingNarrative: false,
      // Doesn't matter which value this carries -- wasExplicitPitchRequest
      // already hides the button on a trigger="generate_pitch" exchange
      // regardless of submissionMode.
      submissionMode: "situation",
    };
    setExchanges((prev) => [...prev, exchange]);
    setIsComposerOpen(false);
    void runExchange(exchange);
  }, [runExchange]);

  const handleRetry = useCallback(
    (exchange: Exchange) => {
      if (exchange.kind === "situation") {
        setExchanges((prev) =>
          prev.map((e) =>
            e.id === exchange.id
              ? { ...e, state: "loading", error: undefined, narrativeText: "", isStreamingNarrative: false }
              : e,
          ),
        );
        void runExchange(exchange);
      } else {
        setExchanges((prev) =>
          prev.map((e) => (e.id === exchange.id ? { ...e, state: "loading", error: undefined } : e)),
        );
        void runHiringSignalExchange(exchange);
      }
    },
    [runExchange, runHiringSignalExchange],
  );

  const handleSelectConversation = useCallback(
    async (conversation: ConversationSummary) => {
      conversationIdRef.current = conversation.id;
      setActiveConversationId(conversation.id);
      setExchanges([]);
      try {
        const result = await getConversationMessages(token!, conversation.id, "bd-chat");
        setHistoryMessages(result.messages);
        // Collapse to the "Ask another question…" pill when the
        // conversation already has messages -- otherwise the full
        // composer (mode tabs included) stays pinned open at the bottom
        // while the user is just reading history. Only a brand-new, empty
        // conversation opens straight into the full composer.
        setIsComposerOpen(result.messages.length === 0);
      } catch {
        setHistoryMessages([]);
        setIsComposerOpen(true);
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
      <div className="top-bar">
        <div className="top-bar-left">
          <button
            type="button"
            className="sidebar-toggle-button"
            aria-label={sidebarOpen ? "Hide conversation history" : "Show conversation history"}
            title={sidebarOpen ? "Hide conversation history" : "Show conversation history"}
            onClick={() => setSidebarOpen((v) => !v)}
          >
            <SidebarIcon />
          </button>
          <span className="top-bar-title">MOTM Sales Director</span>
          <span className="top-bar-title-sub">Business Development</span>
        </div>
        <div className="top-bar-user">
          <span>{user?.email}</span>
          {onOpenAdmin && (
            <button className="link-button" onClick={onOpenAdmin}>
              Admin Dashboard
            </button>
          )}
          <button className="link-button" onClick={onOpenSettings}>
            Settings
          </button>
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
          basePath="bd-chat"
          collapsed={!sidebarOpen}
        />

        <div className="chat-main">
          <div className="chat-scroll">
          <div className="chat-body">
            {isEmpty && (
              <div className="empty-state">
                <p className="empty-state-greeting">Hi {getDisplayName(user)}</p>
                <h2>Describe your BD situation</h2>
                <p>
                  Tell us what's happening — a prospect conversation, an objection, an account
                  you're trying to open. Add the prospect's company/website if you have one, but
                  you can start with just the situation. Or use "Approach a hiring company" below
                  to turn a job posting into an outreach message.
                </p>
              </div>
            )}

            {historyMessages.map((message, index) => {
              if (message.sender === "user") {
                return message.content.startsWith("Company:") ? (
                  <HiringSignalUserTurnCard
                    key={message.id}
                    values={parseStoredHiringSignalContent(message.content)}
                  />
                ) : (
                  <BDUserTurnCard key={message.id} values={parseStoredUserContent(message.content)} />
                );
              }
              if (message.hiring_signal) {
                return (
                  <HistoryMessageCard
                    key={message.id}
                    message={message}
                    token={token!}
                    conversationId={activeConversationId!}
                    basePath="bd-chat"
                  />
                );
              }
              const precedingUser = historyMessages[index - 1];
              const explicitPitch =
                precedingUser?.sender === "user" &&
                !precedingUser.content.startsWith("Company:") &&
                wasExplicitPitchRequest(undefined, parseStoredUserContent(precedingUser.content).situation);
              return (
                <HistoryMessageCard
                  key={message.id}
                  message={message}
                  token={token!}
                  conversationId={activeConversationId!}
                  isLatest={exchanges.length === 0 && index === historyMessages.length - 1}
                  onGeneratePitch={explicitPitch ? undefined : handleGeneratePitch}
                  generatePitchDisabled={isBusy}
                  basePath="bd-chat"
                />
              );
            })}

            {exchanges.map((exchange, index) => (
              <div key={exchange.id} style={{ display: "contents" }}>
                {exchange.kind === "situation" ? (
                  <>
                    <BDUserTurnCard values={exchange.values} />
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
                          exchange.submissionMode === "situation" &&
                          !wasExplicitPitchRequest(
                            exchange.trigger,
                            exchange.values.raw_message || exchange.values.situation,
                          )
                            ? handleGeneratePitch
                            : undefined
                        }
                        generatePitchDisabled={isBusy}
                        basePath="bd-chat"
                      />
                    )}
                  </>
                ) : (
                  <>
                    <HiringSignalUserTurnCard values={exchange.values} />
                    {exchange.state === "loading" && (
                      <LoadingBubble label="Analyzing hiring signal and drafting outreach…" />
                    )}
                    {exchange.state === "success" && exchange.result && (
                      <HiringSignalResultCard result={exchange.result} />
                    )}
                  </>
                )}
                {exchange.state === "error" && exchange.error && (
                  <ChatErrorNotice error={exchange.error} onRetry={() => handleRetry(exchange)} />
                )}
              </div>
            ))}
          </div>
          </div>

          <div className="composer">
            <div className="composer-inner">
              {isComposerOpen ? (
                <>
                  <div className="mode-tabs">
                    <button
                      type="button"
                      className={`mode-tab${composerMode === "situation" ? " active" : ""}`}
                      onClick={() => setComposerMode(composerMode === "situation" ? "plain" : "situation")}
                    >
                      <span>Describe a situation</span>
                      <span
                        className="mode-tab-close"
                        role="button"
                        aria-label="Close describe-a-situation mode"
                        onClick={(e) => {
                          e.stopPropagation();
                          setComposerMode("plain");
                        }}
                      >
                        <XIcon />
                      </span>
                    </button>
                    <button
                      type="button"
                      className={`mode-tab${composerMode === "hiring_signal" ? " active" : ""}`}
                      onClick={() => setComposerMode(composerMode === "hiring_signal" ? "plain" : "hiring_signal")}
                    >
                      <span>Approach a hiring company</span>
                      <span
                        className="mode-tab-close"
                        role="button"
                        aria-label="Close approach-a-hiring-company mode"
                        onClick={(e) => {
                          e.stopPropagation();
                          setComposerMode("plain");
                        }}
                      >
                        <XIcon />
                      </span>
                    </button>
                  </div>
                  {composerMode === "hiring_signal" ? (
                    <HiringSignalComposer
                      onSubmit={handleSubmitHiringSignal}
                      disabled={isBusy}
                      onCancel={() => setComposerMode("plain")}
                    />
                  ) : (
                    <BDComposer onSubmit={handleSubmit} disabled={isBusy} showDetailsPanel={composerMode === "situation"} />
                  )}
                </>
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
