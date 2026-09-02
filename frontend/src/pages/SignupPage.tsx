import { useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";
import type { AppRole } from "../api/types";

// The only two roles POST /auth/signup accepts -- see SIGNUP_ALLOWED_ROLES
// in app/routers/auth.py. Keep this list in sync with that set; the backend
// is the actual enforcement point, this is just what the picker offers.
const SIGNUP_ROLES: { role: AppRole; label: string; description: string }[] = [
  { role: "motm_bd", label: "Business Development", description: "You sell MOTM itself." },
  {
    role: "motm_sales_engineer",
    label: "Sales Engineer",
    description: "You support an MOTM customer's own sale.",
  },
];

export default function SignupPage({ onSwitchToLogin }: { onSwitchToLogin: () => void }) {
  const { signup, signupError, signupSuccessMessage, isSubmittingSignup } = useAuth();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<AppRole>("motm_bd");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!fullName || !email || !password) return;
    void signup({ full_name: fullName, email, password, role });
  }

  if (signupSuccessMessage) {
    return (
      <div className="centered-screen">
        <div className="auth-card">
          <p className="brand">MOTM Sales Director</p>
          <p className="brand-sub">Account created</p>
          <p style={{ color: "var(--text-muted)", fontSize: 14, lineHeight: 1.5 }}>
            {signupSuccessMessage}
          </p>
          <div style={{ marginTop: 20 }}>
            <button type="button" className="primary-button" onClick={onSwitchToLogin}>
              Back to sign in
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="centered-screen">
      <div className="auth-card">
        <p className="brand">MOTM Sales Director</p>
        <p className="brand-sub">Create your account</p>

        <form onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="full_name">Name</label>
            <input
              id="full_name"
              type="text"
              autoComplete="name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              required
            />
          </div>
          <div className="field">
            <label htmlFor="signup_email">Email</label>
            <input
              id="signup_email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          <div className="field">
            <label htmlFor="signup_password">Password</label>
            <input
              id="signup_password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={8}
              required
            />
          </div>
          <div className="field">
            <label>Workspace</label>
            <div className="role-toggle" role="radiogroup" aria-label="Workspace">
              {SIGNUP_ROLES.map((option) => (
                <button
                  key={option.role}
                  type="button"
                  role="radio"
                  aria-checked={role === option.role}
                  className={`role-option${role === option.role ? " selected" : ""}`}
                  onClick={() => setRole(option.role)}
                >
                  <strong>{option.label}</strong>
                  <span>{option.description}</span>
                </button>
              ))}
            </div>
          </div>
          <button type="submit" className="primary-button" disabled={isSubmittingSignup}>
            {isSubmittingSignup ? "Creating account…" : "Create account"}
          </button>
          {signupError && <p className="error-text">{signupError}</p>}
        </form>

        <div style={{ marginTop: 20 }}>
          <button type="button" className="link-button" onClick={onSwitchToLogin}>
            Already have an account? Sign in
          </button>
        </div>
      </div>
    </div>
  );
}
