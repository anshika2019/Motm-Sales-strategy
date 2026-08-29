import { useAuth } from "../auth/AuthContext";

export default function NotAuthorizedPage() {
  const { user, logout } = useAuth();

  return (
    <div className="centered-screen">
      <div className="auth-card not-authorized-card">
        <h2>Access not enabled</h2>
        <p>
          Signed in as <strong>{user?.email}</strong>, but this account doesn't have Business
          Development or Sales Engineer access yet. Ask an admin to grant the{" "}
          <code>motm_bd</code> or <code>motm_sales_engineer</code> role to this account.
        </p>
        <div style={{ marginTop: 20 }}>
          <button className="link-button" onClick={logout}>
            Sign in with a different account
          </button>
        </div>
      </div>
    </div>
  );
}
