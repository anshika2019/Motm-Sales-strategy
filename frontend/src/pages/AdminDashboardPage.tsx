import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiException, approveUser, deleteUser, listUsers } from "../api/client";
import type { UserWithRoles } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import ThemeToggle from "../components/ThemeToggle";
import {
  BriefcaseIcon,
  CheckIcon,
  ClockIcon,
  SearchIcon,
  SettingsIcon,
  TrashIcon,
  UsersIcon,
  WrenchIcon,
  XIcon,
} from "../components/chat/icons";

type LoadState = "loading" | "success" | "error";

// Business Development / Sales Engineering lists only ever show approved,
// active users -- a pending self-service signup already holds its
// self-granted role (see SIGNUP_ALLOWED_ROLES in app/routers/auth.py) but
// can't log in yet, so it sits in its own "Pending Approval" section
// instead until an admin approves or rejects it.
function partitionUsers(users: UserWithRoles[]) {
  return {
    pending: users.filter((u) => !u.is_approved),
    bd: users.filter((u) => u.is_approved && u.roles.includes("motm_bd")),
    se: users.filter((u) => u.is_approved && u.roles.includes("motm_sales_engineer")),
  };
}

function roleLabel(role: string) {
  if (role === "motm_bd") return "BD";
  if (role === "motm_sales_engineer") return "SE";
  if (role === "admin") return "Admin";
  if (role === "sales_manager") return "Sales Manager";
  if (role === "knowledge_manager") return "Knowledge Manager";
  return role;
}

function matchesQuery(u: UserWithRoles, query: string) {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (u.full_name ?? "").toLowerCase().includes(q) || u.email.toLowerCase().includes(q);
}

function initials(u: UserWithRoles) {
  const source = u.full_name?.trim() || u.email;
  const parts = source.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return source.slice(0, 2).toUpperCase();
}

interface StatCardProps {
  label: string;
  value: number;
  icon: ReactNode;
}

function StatCard({ label, value, icon }: StatCardProps) {
  return (
    <div className="admin-stat-card">
      <div className="admin-stat-icon">{icon}</div>
      <div>
        <div className="admin-stat-value">{value}</div>
        <div className="admin-stat-label">{label}</div>
      </div>
    </div>
  );
}

interface UserSectionProps {
  title: string;
  icon: ReactNode;
  users: UserWithRoles[];
  totalInGroup: number;
  confirmDeleteId: string | null;
  onArmDelete: (id: string) => void;
  onCancelDelete: () => void;
  onConfirmDelete: (user: UserWithRoles) => void;
}

function UserSection({
  title,
  icon,
  users,
  totalInGroup,
  confirmDeleteId,
  onArmDelete,
  onCancelDelete,
  onConfirmDelete,
}: UserSectionProps) {
  return (
    <div className="admin-section">
      <div className="admin-section-header">
        <h2 className="admin-section-title">
          <span className="admin-section-icon">{icon}</span>
          {title}
          <span className="admin-section-count">{totalInGroup}</span>
        </h2>
      </div>
      {totalInGroup === 0 ? (
        <div className="admin-empty">No users in this group yet.</div>
      ) : users.length === 0 ? (
        <div className="admin-empty">No matches for your search.</div>
      ) : (
        <div className="admin-user-list">
          {users.map((u) => {
            const isConfirming = confirmDeleteId === u.id;
            return (
              <div key={u.id} className={`admin-user-row${isConfirming ? " confirming" : ""}`}>
                <div className="admin-user-avatar">{initials(u)}</div>
                <div className="admin-user-info">
                  <span className="admin-user-name">{u.full_name ?? "(no name)"}</span>
                  <span className="admin-user-email">{u.email}</span>
                </div>
                {u.roles.includes("motm_bd") && u.roles.includes("motm_sales_engineer") && (
                  <span className="admin-role-pill">Dual role</span>
                )}
                <div className="admin-user-actions">
                  {isConfirming ? (
                    <>
                      <button
                        className="history-action-btn danger"
                        title="Confirm remove"
                        aria-label="Confirm remove user"
                        onClick={() => onConfirmDelete(u)}
                      >
                        <CheckIcon />
                      </button>
                      <button
                        className="history-action-btn"
                        title="Cancel"
                        aria-label="Cancel remove user"
                        onClick={onCancelDelete}
                      >
                        <XIcon />
                      </button>
                    </>
                  ) : (
                    <button
                      className="history-action-btn danger"
                      title="Remove user"
                      aria-label="Remove user"
                      onClick={() => onArmDelete(u.id)}
                    >
                      <TrashIcon />
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

interface PendingSectionProps {
  users: UserWithRoles[];
  totalPending: number;
  confirmRejectId: string | null;
  approvingId: string | null;
  onApprove: (user: UserWithRoles) => void;
  onArmReject: (id: string) => void;
  onCancelReject: () => void;
  onConfirmReject: (user: UserWithRoles) => void;
}

function PendingSection({
  users,
  totalPending,
  confirmRejectId,
  approvingId,
  onApprove,
  onArmReject,
  onCancelReject,
  onConfirmReject,
}: PendingSectionProps) {
  if (totalPending === 0) return null;

  return (
    <div className="admin-section admin-pending-section">
      <div className="admin-section-header">
        <h2 className="admin-section-title">
          <span className="admin-section-icon pending">
            <ClockIcon />
          </span>
          Pending Approval
          <span className="admin-section-count pending">{totalPending}</span>
        </h2>
      </div>
      <div className="admin-user-list">
        {users.map((u) => {
          const isConfirming = confirmRejectId === u.id;
          const isApproving = approvingId === u.id;
          return (
            <div key={u.id} className={`admin-user-row${isConfirming ? " confirming" : ""}`}>
              <div className="admin-user-avatar">{initials(u)}</div>
              <div className="admin-user-info">
                <span className="admin-user-name">{u.full_name ?? "(no name)"}</span>
                <span className="admin-user-email">{u.email}</span>
              </div>
              {u.roles.map((role) => (
                <span key={role} className="admin-role-pill neutral">
                  {roleLabel(role)}
                </span>
              ))}
              <div className="admin-user-actions">
                {isConfirming ? (
                  <>
                    <button
                      className="history-action-btn danger"
                      title="Confirm reject"
                      aria-label="Confirm reject signup"
                      onClick={() => onConfirmReject(u)}
                    >
                      <CheckIcon />
                    </button>
                    <button
                      className="history-action-btn"
                      title="Cancel"
                      aria-label="Cancel reject"
                      onClick={onCancelReject}
                    >
                      <XIcon />
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      className="history-action-btn success"
                      title="Approve"
                      aria-label="Approve user"
                      disabled={isApproving}
                      onClick={() => onApprove(u)}
                    >
                      <CheckIcon />
                    </button>
                    <button
                      className="history-action-btn danger"
                      title="Reject"
                      aria-label="Reject signup"
                      onClick={() => onArmReject(u.id)}
                    >
                      <TrashIcon />
                    </button>
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

interface AdminDashboardPageProps {
  // Present only when this account also holds a workspace role (SE/BD) --
  // lets a dual-role user switch back to their normal chat workspace. See
  // App.tsx.
  onBackToWorkspace?: () => void;
  onOpenSettings: () => void;
}

export default function AdminDashboardPage({ onBackToWorkspace, onOpenSettings }: AdminDashboardPageProps) {
  const { token, user, logout, handleUnauthorized } = useAuth();
  const [state, setState] = useState<LoadState>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [users, setUsers] = useState<UserWithRoles[]>([]);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [approvingId, setApprovingId] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  function loadUsers() {
    if (!token) return;
    setState("loading");
    listUsers(token)
      .then((result) => {
        setUsers(result);
        setState("success");
      })
      .catch((err) => {
        if (err instanceof ApiException) {
          if (err.error.kind === "unauthorized" || err.error.kind === "forbidden") {
            handleUnauthorized();
            return;
          }
          setErrorMessage(err.message);
        } else {
          setErrorMessage("Something went wrong loading users.");
        }
        setState("error");
      });
  }

  useEffect(() => {
    loadUsers();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function confirmDelete(target: UserWithRoles) {
    if (!token) return;
    try {
      await deleteUser(token, target.id);
      setUsers((prev) => prev.filter((u) => u.id !== target.id));
    } catch {
      // Keep the row in place so the admin can retry the removal.
    }
    setConfirmDeleteId(null);
  }

  async function handleApprove(target: UserWithRoles) {
    if (!token) return;
    setApprovingId(target.id);
    try {
      await approveUser(token, target.id);
      setUsers((prev) =>
        prev.map((u) => (u.id === target.id ? { ...u, is_approved: true } : u)),
      );
    } catch {
      // Keep the row in place so the admin can retry the approval.
    }
    setApprovingId(null);
  }

  const { pending, bd, se } = useMemo(() => partitionUsers(users), [users]);
  const filteredPending = useMemo(
    () => pending.filter((u) => matchesQuery(u, query)),
    [pending, query],
  );
  const filteredBd = useMemo(() => bd.filter((u) => matchesQuery(u, query)), [bd, query]);
  const filteredSe = useMemo(() => se.filter((u) => matchesQuery(u, query)), [se, query]);

  return (
    <div className="app-shell">
      <div className="top-bar">
        <div className="top-bar-left">
          <span className="top-bar-title">MOTM Sales Director</span>
          <span className="top-bar-title-sub">Admin Dashboard</span>
        </div>
        <div className="top-bar-user">
          <span>{user?.email}</span>
          {onBackToWorkspace && (
            <button className="link-button" onClick={onBackToWorkspace}>
              Back to workspace
            </button>
          )}
          <button className="link-button" onClick={onOpenSettings} title="Settings" aria-label="Settings">
            <SettingsIcon />
          </button>
          <button className="link-button" onClick={() => logout()}>
            Sign out
          </button>
          <ThemeToggle />
        </div>
      </div>

      <div className="admin-dashboard">
        {state === "loading" && <p className="loading-text">Loading users…</p>}
        {state === "error" && (
          <div className="admin-error">
            <p>{errorMessage}</p>
            <button className="retry-button" onClick={loadUsers}>
              Retry
            </button>
          </div>
        )}
        {state === "success" && (
          <>
            <div className="admin-page-header">
              <div>
                <h1 className="admin-page-title">User Management</h1>
                <p className="admin-page-subtitle">
                  Manage Business Development and Sales Engineering accounts
                </p>
              </div>
              <div className="admin-search">
                <SearchIcon />
                <input
                  type="text"
                  placeholder="Search by name or email…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </div>
            </div>

            <div className="admin-stats">
              <StatCard label="Total users" value={users.length} icon={<UsersIcon />} />
              <StatCard label="Pending approval" value={pending.length} icon={<ClockIcon />} />
              <StatCard label="Business Development" value={bd.length} icon={<BriefcaseIcon />} />
              <StatCard label="Sales Engineering" value={se.length} icon={<WrenchIcon />} />
            </div>

            <PendingSection
              users={filteredPending}
              totalPending={pending.length}
              confirmRejectId={confirmDeleteId}
              approvingId={approvingId}
              onApprove={handleApprove}
              onArmReject={setConfirmDeleteId}
              onCancelReject={() => setConfirmDeleteId(null)}
              onConfirmReject={confirmDelete}
            />

            <div className="admin-sections-grid">
              <UserSection
                title="Business Development"
                icon={<BriefcaseIcon />}
                users={filteredBd}
                totalInGroup={bd.length}
                confirmDeleteId={confirmDeleteId}
                onArmDelete={setConfirmDeleteId}
                onCancelDelete={() => setConfirmDeleteId(null)}
                onConfirmDelete={confirmDelete}
              />
              <UserSection
                title="Sales Engineering"
                icon={<WrenchIcon />}
                users={filteredSe}
                totalInGroup={se.length}
                confirmDeleteId={confirmDeleteId}
                onArmDelete={setConfirmDeleteId}
                onCancelDelete={() => setConfirmDeleteId(null)}
                onConfirmDelete={confirmDelete}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
