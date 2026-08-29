import { useState } from "react";
import { useAuth } from "./auth/AuthContext";
import LoginPage from "./pages/LoginPage";
import SignupPage from "./pages/SignupPage";
import ChatPage from "./pages/ChatPage";
import BDChatPage from "./pages/BDChatPage";
import NotAuthorizedPage from "./pages/NotAuthorizedPage";

export default function App() {
  const { status, workspace } = useAuth();
  // Only meaningful while status === "anonymous" -- reset isn't needed on
  // successful auth since this component unmounts the login/signup screens
  // entirely once status moves past "anonymous".
  const [authView, setAuthView] = useState<"login" | "signup">("login");

  if (status === "authenticated") {
    // Routed straight to the workspace matching the account's role -- see
    // Workspace's docstring in AuthContext.tsx.
    return workspace === "bd" ? <BDChatPage /> : <ChatPage />;
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
