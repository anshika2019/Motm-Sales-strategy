import type {
  ApiError,
  BDHiringSignalRequest,
  BDHiringSignalResponse,
  BDStrategyRequest,
  ConversationMessagesResponse,
  ConversationSummary,
  CreateConversationResponse,
  FeedbackRating,
  FeedbackResponse,
  LoginRequest,
  LoginResponse,
  MeResponse,
  MessageResponse,
  PydanticValidationItem,
  SignupRequest,
  StrategyRequest,
  StrategyResponse,
} from "./types";

// Which router these conversation/message/strategy endpoints hit --
// "chat" is the existing SE pipeline, "bd-chat" is the BD pipeline (see
// app/routers/chat.py / app/routers/bd_chat.py). Every function below that
// takes a `base` parameter defaults to "chat" so existing SE call sites
// (ChatPage.tsx and friends) are unaffected; BD's screens pass "bd-chat"
// explicitly.
export type ApiBase = "chat" | "bd-chat";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

function describeError(error: ApiError): string {
  if (error.kind === "quality_gate") return error.detail.message;
  if (error.kind === "validation") return "Please check your inputs and try again.";
  return error.message;
}

export class ApiException extends Error {
  error: ApiError;
  constructor(error: ApiError) {
    super(describeError(error));
    this.error = error;
  }
}

function toApiError(status: number, body: unknown): ApiError {
  const detail = (body as { detail?: unknown } | null)?.detail;

  if (status === 401) {
    return { kind: "unauthorized", message: typeof detail === "string" ? detail : "Invalid or expired token" };
  }
  if (status === 403) {
    return { kind: "forbidden", message: typeof detail === "string" ? detail : "You do not have permission to perform this action" };
  }
  if (status === 404) {
    return { kind: "not_found", message: typeof detail === "string" ? detail : "Not found" };
  }
  if (status === 400) {
    return { kind: "bad_url", message: typeof detail === "string" ? detail : "Bad request" };
  }
  if (status === 409) {
    return { kind: "conflict", message: typeof detail === "string" ? detail : "Already exists" };
  }
  if (status === 422) {
    if (Array.isArray(detail)) {
      return { kind: "validation", items: detail as PydanticValidationItem[] };
    }
    if (detail && typeof detail === "object" && "missing" in (detail as object)) {
      const d = detail as { message?: string; missing?: string[] };
      return { kind: "quality_gate", detail: { message: d.message ?? "", missing: d.missing ?? [] } };
    }
    return { kind: "validation", items: [] };
  }
  return { kind: "server", message: `Request failed with status ${status}` };
}

async function request<T>(
  path: string,
  options: { method?: string; token?: string | null; body?: unknown } = {},
): Promise<T> {
  const { method = "GET", token, body } = options;

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiException({ kind: "network", message: "Couldn't reach the server — check that the backend is running." });
  }

  const text = await response.text();
  const parsed = text ? JSON.parse(text) : null;

  if (!response.ok) {
    throw new ApiException(toApiError(response.status, parsed));
  }

  return parsed as T;
}

export function login(body: LoginRequest): Promise<LoginResponse> {
  return request<LoginResponse>("/auth/login", { method: "POST", body });
}

// Returns a session immediately (same shape as login()) -- the backend logs
// the new account in as its last step, so no separate /auth/login call is
// needed after signing up.
export function signup(body: SignupRequest): Promise<LoginResponse> {
  return request<LoginResponse>("/auth/signup", { method: "POST", body });
}

export function fetchMe(token: string): Promise<MeResponse> {
  return request<MeResponse>("/auth/me", { token });
}

export function createConversation(
  token: string,
  base: ApiBase = "chat",
): Promise<CreateConversationResponse> {
  return request<CreateConversationResponse>(`/${base}/conversations`, { method: "POST", token });
}

export function postStrategy(
  token: string,
  conversationId: string,
  body: StrategyRequest,
): Promise<StrategyResponse> {
  return request<StrategyResponse>(`/chat/conversations/${conversationId}/strategy`, {
    method: "POST",
    token,
    body,
  });
}

export function listConversations(
  token: string,
  base: ApiBase = "chat",
): Promise<ConversationSummary[]> {
  return request<ConversationSummary[]>(`/${base}/conversations`, { token });
}

export function renameConversation(
  token: string,
  conversationId: string,
  title: string,
  base: ApiBase = "chat",
): Promise<ConversationSummary> {
  return request<ConversationSummary>(`/${base}/conversations/${conversationId}`, {
    method: "PATCH",
    token,
    body: { title },
  });
}

export function deleteConversation(
  token: string,
  conversationId: string,
  base: ApiBase = "chat",
): Promise<void> {
  return request<void>(`/${base}/conversations/${conversationId}`, { method: "DELETE", token });
}

export function getConversationMessages(
  token: string,
  conversationId: string,
  base: ApiBase = "chat",
): Promise<ConversationMessagesResponse> {
  return request<ConversationMessagesResponse>(`/${base}/conversations/${conversationId}/messages`, {
    token,
  });
}

export function submitFeedback(
  token: string,
  conversationId: string,
  messageId: string,
  rating: FeedbackRating,
  comment?: string | null,
  base: ApiBase = "chat",
): Promise<FeedbackResponse> {
  return request<FeedbackResponse>(
    `/${base}/conversations/${conversationId}/messages/${messageId}/feedback`,
    { method: "POST", token, body: { rating, comment: comment || null } },
  );
}

// Non-streaming -- the hiring-signal agent runs two sequential LLM calls
// server-side (see app/routers/bd_chat.py's post_bd_hiring_signal_outreach)
// and returns the finished package in one response, unlike /strategy's SSE
// stream.
export function postBDHiringSignalOutreach(
  token: string,
  conversationId: string,
  body: BDHiringSignalRequest,
): Promise<BDHiringSignalResponse> {
  return request<BDHiringSignalResponse>(
    `/bd-chat/conversations/${conversationId}/hiring-signal-outreach`,
    { method: "POST", token, body },
  );
}

export interface StrategyStreamHandlers {
  onNarrativeChunk: (delta: string) => void;
  onResult: (result: MessageResponse) => void;
  onError: (message: string) => void;
}

/**
 * Streams a /strategy/stream turn via fetch + ReadableStream (not
 * EventSource -- EventSource can't send a POST body or an Authorization
 * header). Parses SSE frames ("event: x\ndata: y\n\n") out of the decoded
 * text as they arrive. A non-2xx initial response is surfaced through
 * onError using the same error shaping as the rest of the client.
 */
export async function postStrategyStream(
  token: string,
  conversationId: string,
  body: StrategyRequest | BDStrategyRequest,
  handlers: StrategyStreamHandlers,
  base: ApiBase = "chat",
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}/${base}/conversations/${conversationId}/strategy/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(body),
    });
  } catch {
    handlers.onError("Couldn't reach the server — check that the backend is running.");
    return;
  }

  if (!response.ok) {
    const text = await response.text();
    const parsed = text ? JSON.parse(text) : null;
    const error = toApiError(response.status, parsed);
    throw new ApiException(error);
  }

  if (!response.body) {
    handlers.onError("Streaming is not supported by this response.");
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      const lines = frame.split("\n");
      let event = "message";
      let data = "";
      for (const line of lines) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data = line.slice(6);
      }
      if (!data) continue;
      const parsed = JSON.parse(data);

      if (event === "narrative_chunk") handlers.onNarrativeChunk(parsed.delta);
      else if (event === "result") handlers.onResult(parsed as MessageResponse);
      else if (event === "error") handlers.onError(parsed.message ?? "Something went wrong.");
    }
  }
}
