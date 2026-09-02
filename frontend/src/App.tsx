import { useEffect, useState } from "react";
import { useAuth } from "./auth/AuthContext";
import LoginPage from "./pages/LoginPage";
import SignupPage from "./pages/SignupPage";
import ChatPage from "./pages/ChatPage";
import BDChatPage from "./pages/BDChatPage";
import AdminDashboardPage from "./pages/AdminDashboardPage";
import SettingsPage from "./pages/SettingsPage";
import NotAuthorizedPage from "./pages/NotAuthorizedPage";

export default function App() {
  const { status, workspace, user } = useAuth();
  // Only meaningful while status === "anonymous" -- reset isn't needed on
  // successful auth since this component unmounts the login/signup screens
  // entirely once status moves past "anonymous".
  const [authView, setAuthView] = useState<"login" | "signup">("login");
  // Lets a dual-role account (admin + SE/BD) switch between the admin
  // dashboard and their normal chat workspace -- workspace itself only
  // decides the *default* landing screen (see workspaceForRoles's
  // docstring in AuthContext.tsx); this local toggle overrides it without
  // touching that routing logic. Reset on logout below.
  const [showAdmin, setShowAdmin] = useState(false);
  // Same idea as showAdmin above -- a local override, independent of
  // `workspace`, that any authenticated account (admin or not) can reach
  // from its own workspace's header. See ChatPage.tsx/BDChatPage.tsx's
  // "Settings" link and AdminDashboardPage.tsx's.
  const [showSettings, setShowSettings] = useState(false);
  // Resets the toggles on logout so a later login (possibly by a different
  // account in the same tab) doesn't inherit the previous session's choice.
  useEffect(() => {
    if (status !== "authenticated") {
      setShowAdmin(false);
      setShowSettings(false);
    }
  }, [status]);

  if (status === "authenticated") {
    if (showSettings) {
      return <SettingsPage onBack={() => setShowSettings(false)} />;
    }
    // Routed straight to the workspace matching the account's role -- see
    // Workspace's docstring in AuthContext.tsx.
    const isAdmin = user?.roles.includes("admin") ?? false;
    const onOpenSettings = () => setShowSettings(true);
    if (workspace === "admin" || (isAdmin && showAdmin)) {
      return (
        <AdminDashboardPage
          onBackToWorkspace={workspace !== "admin" ? () => setShowAdmin(false) : undefined}
          onOpenSettings={onOpenSettings}
        />
      );
    }
    const onOpenAdmin = isAdmin ? () => setShowAdmin(true) : undefined;
    return workspace === "bd" ? (
      <BDChatPage onOpenAdmin={onOpenAdmin} onOpenSettings={onOpenSettings} />
    ) : (
      <ChatPage onOpenAdmin={onOpenAdmin} onOpenSettings={onOpenSettings} />
    );
  }
  if (status === "unauthorized") return <NotAuthorizedPage />;
  if (status === "checking") {
    return (
      <div className="centered-screen">
        <p style={{ color: "var(--text-muted)" }}>Signing you in…</p>
      </div>
    );
  }
  return authView === "signup" ? (
    <SignupPage onSwitchToLogin={() => setAuthView("login")} />
  ) : (
    <LoginPage onSwitchToSignup={() => setAuthView("signup")} />
  );
}
