import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { deleteConversation, listConversations, renameConversation, type ApiBase } from "../../api/client";
import type { ConversationSummary } from "../../api/types";
import { CheckIcon, PencilIcon, TrashIcon, XIcon } from "./icons";

function groupByRecency(conversations: ConversationSummary[]) {
  const todayStr = new Date().toDateString();
  const today: ConversationSummary[] = [];
  const earlier: ConversationSummary[] = [];
  for (const conv of conversations) {
    (new Date(conv.updated_at).toDateString() === todayStr ? today : earlier).push(conv);
  }
  return { today, earlier };
}

interface HistoryPanelProps {
  token: string;
  activeConversationId: string | null;
  onSelectConversation: (conversation: ConversationSummary) => void;
  onNewConversation: () => void;
  onConversationDeleted: (conversationId: string) => void;
  refreshSignal: number;
  /** Which router this panel's conversations live under -- see ApiBase in
   * api/client.ts. Defaults to "chat" (SE) so existing callers are
   * unaffected; BD's screen passes "bd-chat". */
  basePath?: ApiBase;
  /** Collapses the panel to zero width (desktop) or slides it off-screen
   * (mobile drawer) via CSS only -- content stays mounted so the width/
   * transform transition has something to animate, rather than the panel
   * popping in empty. See ChatPage.tsx/BDChatPage.tsx's sidebarOpen. */
  collapsed?: boolean;
}

export default function HistoryPanel({
  token,
  activeConversationId,
  onSelectConversation,
  onNewConversation,
  onConversationDeleted,
  refreshSignal,
  basePath = "chat",
  collapsed = false,
}: HistoryPanelProps) {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listConversations(token, basePath)
      .then((result) => {
        if (!cancelled) setConversations(result);
      })
      .catch(() => {
        if (!cancelled) setConversations([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token, refreshSignal, basePath]);

  useEffect(() => {
    if (renamingId) renameInputRef.current?.focus();
  }, [renamingId]);

  function startRename(conv: ConversationSummary) {
    setConfirmDeleteId(null);
    setRenamingId(conv.id);
    setRenameValue(conv.title ?? "");
  }

  function cancelRename() {
    setRenamingId(null);
    setRenameValue("");
  }

  async function submitRename(conv: ConversationSummary) {
    // Guards against the stray extra call that fires when the input
    // unmounts (Enter -> cancelRename -> DOM removal synthesizes a blur,
    // which would otherwise re-enter this function a second time).
    if (renamingId !== conv.id) return;

    const title = renameValue.trim();
    if (!title || title === (conv.title ?? "")) {
      cancelRename();
      return;
    }
    cancelRename();

    // Apply immediately so the sidebar reflects the new title without
    // waiting on the round-trip; reconciled with the server response (or
    // rolled back on failure) below.
    const previousTitle = conv.title;
    setConversations((prev) => prev.map((c) => (c.id === conv.id ? { ...c, title } : c)));
    try {
      const updated = await renameConversation(token, conv.id, title, basePath);
      setConversations((prev) => prev.map((c) => (c.id === conv.id ? updated : c)));
    } catch {
      setConversations((prev) =>
        prev.map((c) => (c.id === conv.id ? { ...c, title: previousTitle } : c)),
      );
    }
  }

  function handleRenameKeyDown(e: KeyboardEvent<HTMLInputElement>, conv: ConversationSummary) {
    if (e.key === "Enter") {
      e.preventDefault();
      void submitRename(conv);
    } else if (e.key === "Escape") {
      e.preventDefault();
      cancelRename();
    }
  }

  async function confirmDelete(conv: ConversationSummary) {
    try {
      await deleteConversation(token, conv.id, basePath);
      setConversations((prev) => prev.filter((c) => c.id !== conv.id));
      onConversationDeleted(conv.id);
    } catch {
      // Keep the item in place so the user can retry the delete.
    }
    setConfirmDeleteId(null);
  }

  const renderHistoryItem = (conv: ConversationSummary) => {
    const isRenaming = renamingId === conv.id;
    const isConfirmingDelete = confirmDeleteId === conv.id;

    return (
      <div
        key={conv.id}
        className={`history-item ${conv.id === activeConversationId ? "active" : ""}${
          isRenaming || isConfirmingDelete ? " editing" : ""
        }`}
        onClick={() => !isRenaming && onSelectConversation(conv)}
      >
        {isRenaming ? (
          <input
            ref={renameInputRef}
            className="history-rename-input"
            value={renameValue}
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => handleRenameKeyDown(e, conv)}
            onBlur={() => void submitRename(conv)}
          />
        ) : (
          <p className="history-title">{conv.title ?? "Untitled conversation"}</p>
        )}

        <div className="history-item-row">
          <span className="history-date">
            {new Date(conv.updated_at).toLocaleDateString(undefined, {
              day: "numeric",
              month: "short",
            })}
          </span>

          {!isRenaming && (
            <div className="history-item-actions">
              {isConfirmingDelete ? (
                <>
                  <button
                    className="history-action-btn danger"
                    title="Confirm delete"
                    aria-label="Confirm delete"
                    onClick={(e) => {
                      e.stopPropagation();
                      void confirmDelete(conv);
                    }}
                  >
                    <CheckIcon />
                  </button>
                  <button
                    className="history-action-btn"
                    title="Cancel"
                    aria-label="Cancel delete"
                    onClick={(e) => {
                      e.stopPropagation();
                      setConfirmDeleteId(null);
                    }}
                  >
                    <XIcon />
                  </button>
                </>
              ) : (
                <>
                  <button
                    className="history-action-btn"
                    title="Rename"
                    aria-label="Rename conversation"
                    onClick={(e) => {
                      e.stopPropagation();
                      startRename(conv);
                    }}
                  >
                    <PencilIcon />
                  </button>
                  <button
                    className="history-action-btn danger"
                    title="Delete"
                    aria-label="Delete conversation"
                    onClick={(e) => {
                      e.stopPropagation();
                      setConfirmDeleteId(conv.id);
                    }}
                  >
                    <TrashIcon />
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    );
  };

  const { today, earlier } = groupByRecency(conversations);

  return (
    <aside className={`history-panel${collapsed ? " collapsed" : ""}`} aria-hidden={collapsed}>
      <button className="history-new-button" onClick={onNewConversation}>
        + New conversation
      </button>

      {loading && <p className="loading-text">Loading…</p>}
      {!loading && conversations.length === 0 && (
        <p className="loading-text">No conversations yet.</p>
      )}
      {today.length > 0 && (
        <>
          <div className="history-group-label">Today</div>
          {today.map(renderHistoryItem)}
        </>
      )}
      {earlier.length > 0 && (
        <>
          <div className="history-group-label">Earlier</div>
          {earlier.map(renderHistoryItem)}
        </>
      )}
    </aside>
  );
}
