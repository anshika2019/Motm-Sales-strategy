import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";

export default function LoginPage({ onSwitchToSignup }: { onSwitchToSignup: () => void }) {
  const { login, loginError, isSubmittingLogin, sessionExpired } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!email || !password) return;
    void login(email, password);
  }

  return (
    <div className="centered-screen">
      <div className="auth-card">
        <p className="brand">MOTM Sales Director</p>
        <p className="brand-sub">Sign in to your Sales Engineer workspace</p>

        {sessionExpired && (
          <p className="error-text" style={{ marginTop: 0, marginBottom: 16 }}>
            Your session expired — please log in again.
          </p>
        )}

        <form onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          <button type="submit" className="primary-button" disabled={isSubmittingLogin}>
            {isSubmittingLogin ? "Signing in…" : "Sign in"}
          </button>
          {loginError && <p className="error-text">{loginError}</p>}
        </form>

        <div style={{ marginTop: 20 }}>
          <button type="button" className="link-button" onClick={onSwitchToSignup}>
            New here? Create an account
          </button>
        </div>
      </div>
    </div>
  );
}
