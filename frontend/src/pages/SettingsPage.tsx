import { useState, type FormEvent } from "react";
import { ApiException, updateEmail, updatePassword, updateProfile } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import ThemeToggle from "../components/ThemeToggle";

interface SettingsPageProps {
  onBack: () => void;
}

type Feedback = { kind: "success" | "error"; text: string } | null;

/**
 * Profile & Settings -- Name/Username save straight to our own profiles
 * table (PATCH /auth/me); Email and Password go through Supabase's own
 * update-user flow instead (PUT /auth/me/email, /auth/me/password in
 * app/routers/auth.py) since those are Supabase Auth's concern, not ours.
 * An email change requires confirming a link Supabase sends -- it never
 * applies immediately, so the profile's displayed email intentionally
 * doesn't change until that's done.
 */
export default function SettingsPage({ onBack }: SettingsPageProps) {
  const { token, user, logout, refreshProfile } = useAuth();

  const [fullName, setFullName] = useState(user?.full_name ?? "");
  const [username, setUsername] = useState(user?.username ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [isSavingProfile, setIsSavingProfile] = useState(false);
  const [profileFeedback, setProfileFeedback] = useState<Feedback>(null);

  const [isChangingPassword, setIsChangingPassword] = useState(false);
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [isSavingPassword, setIsSavingPassword] = useState(false);
  const [passwordFeedback, setPasswordFeedback] = useState<Feedback>(null);

  function describeError(err: unknown, fallback: string): string {
    if (err instanceof ApiException) return err.message;
    return fallback;
  }

  async function handleSaveProfile(e: FormEvent) {
    e.preventDefault();
    setIsSavingProfile(true);
    setProfileFeedback(null);
    try {
      const messages: string[] = [];

      const trimmedName = fullName.trim();
      const trimmedUsername = username.trim();
      const nameChanged = trimmedName !== (user?.full_name ?? "");
      const usernameChanged = trimmedUsername !== (user?.username ?? "");
      if (nameChanged || usernameChanged) {
        await updateProfile(token!, {
          ...(nameChanged ? { full_name: trimmedName } : {}),
          ...(usernameChanged ? { username: trimmedUsername } : {}),
        });
        messages.push("Profile updated successfully.");
      }

      const trimmedEmail = email.trim();
      if (trimmedEmail !== (user?.email ?? "")) {
        const result = await updateEmail(token!, { email: trimmedEmail });
        messages.push(result.message);
      }

      await refreshProfile();

      setProfileFeedback(
        messages.length > 0
          ? { kind: "success", text: messages.join(" ") }
          : { kind: "success", text: "No changes to save." },
      );
    } catch (err) {
      setProfileFeedback({ kind: "error", text: describeError(err, "Unable to update profile. Please try again.") });
    } finally {
      setIsSavingProfile(false);
    }
  }

  async function handleChangePassword(e: FormEvent) {
    e.preventDefault();
    setPasswordFeedback(null);
    if (newPassword.length < 8) {
      setPasswordFeedback({ kind: "error", text: "New password must be at least 8 characters." });
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordFeedback({ kind: "error", text: "New password and confirmation do not match." });
      return;
    }
    setIsSavingPassword(true);
    try {
      const result = await updatePassword(token!, { new_password: newPassword });
      setPasswordFeedback({ kind: "success", text: result.message });
      setNewPassword("");
      setConfirmPassword("");
      setIsChangingPassword(false);
    } catch (err) {
      setPasswordFeedback({ kind: "error", text: describeError(err, "Unable to update password. Please try again.") });
    } finally {
      setIsSavingPassword(false);
    }
  }

  return (
    <div className="app-shell">
      <div className="top-bar">
        <div className="top-bar-left">
          <span className="top-bar-title">MOTM Sales Director</span>
          <span className="top-bar-title-sub">Profile &amp; Settings</span>
        </div>
        <div className="top-bar-user">
          <span>{user?.email}</span>
          <button className="link-button" onClick={onBack}>
            Back to workspace
          </button>
          <button className="link-button" onClick={() => logout()}>
            Sign out
          </button>
          <ThemeToggle />
        </div>
      </div>

      <div className="settings-page">
        <div className="settings-header">
          <h1 className="settings-title">Profile &amp; Settings</h1>
          <p className="settings-subtitle">Manage your account details and security.</p>
        </div>

        <form className="settings-card" onSubmit={handleSaveProfile}>
          <div className="settings-section-label">Profile</div>

          <div className="settings-field">
            <label htmlFor="settings-name">Name</label>
            <input
              id="settings-name"
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Your full name"
              autoComplete="name"
            />
          </div>

          <div className="settings-field">
            <label htmlFor="settings-email">Email</label>
            <input
              id="settings-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
            <p className="settings-hint">
              Changing this sends a confirmation link to your new address — it only takes effect once you confirm it.
            </p>
          </div>

          <div className="settings-field">
            <label htmlFor="settings-username">Username</label>
            <input
              id="settings-username"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Choose a username"
              autoComplete="username"
            />
          </div>

          {profileFeedback && (
            <p className={profileFeedback.kind === "success" ? "settings-message success" : "settings-message error"}>
              {profileFeedback.kind === "success" ? "✓ " : ""}
              {profileFeedback.text}
            </p>
          )}

          <div className="settings-actions">
            <button type="submit" className="primary-button" style={{ width: "auto" }} disabled={isSavingProfile}>
              {isSavingProfile ? "Saving…" : "Save changes"}
            </button>
          </div>
        </form>

        <div className="settings-card">
          <div className="settings-section-label">Security</div>

          <div className="settings-field">
            <label>Password</label>
            {!isChangingPassword ? (
              <button
                type="button"
                className="secondary-button"
                onClick={() => {
                  setIsChangingPassword(true);
                  setPasswordFeedback(null);
                }}
              >
                Change password
              </button>
            ) : (
              <form className="settings-password-form" onSubmit={handleChangePassword}>
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  placeholder="New password"
                  autoComplete="new-password"
                />
                <input
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="Confirm new password"
                  autoComplete="new-password"
                />
                <div className="settings-actions">
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => {
                      setIsChangingPassword(false);
                      setNewPassword("");
                      setConfirmPassword("");
                      setPasswordFeedback(null);
                    }}
                    disabled={isSavingPassword}
                  >
                    Cancel
                  </button>
                  <button type="submit" className="primary-button" style={{ width: "auto" }} disabled={isSavingPassword}>
                    {isSavingPassword ? "Updating…" : "Update password"}
                  </button>
                </div>
              </form>
            )}
          </div>

          {passwordFeedback && (
            <p className={passwordFeedback.kind === "success" ? "settings-message success" : "settings-message error"}>
              {passwordFeedback.kind === "success" ? "✓ " : ""}
              {passwordFeedback.text}
            </p>
          )}
        </div>

        <div className="settings-card">
          <div className="settings-section-label">Account</div>
          <div className="settings-field">
            <label>Workspace</label>
            <p className="settings-static-value">
              {user?.roles.includes("motm_bd") && "Business Development"}
              {user?.roles.includes("motm_sales_engineer") && "Sales Engineering"}
              {user?.roles.includes("admin") &&
                !user.roles.includes("motm_bd") &&
                !user.roles.includes("motm_sales_engineer") &&
                "Admin"}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
