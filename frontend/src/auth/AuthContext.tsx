import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ApiException,
  fetchMe,
  login as apiLogin,
  refreshSession,
  signup as apiSignup,
} from "../api/client";
import type { AppRole, LoginResponse, MeResponse, SignupRequest } from "../api/types";

const TOKEN_STORAGE_KEY = "motm_access_token";
const REFRESH_TOKEN_STORAGE_KEY = "motm_refresh_token";
const EXPIRES_AT_STORAGE_KEY = "motm_token_expires_at";
const SALES_ENGINEER_ROLE: AppRole = "motm_sales_engineer";
const BD_ROLE: AppRole = "motm_bd";
const ADMIN_ROLE: AppRole = "admin";

// Refresh this many ms before the access token would actually expire, so a
// request in flight right at the boundary doesn't race a 401. Floored at
// MIN_REFRESH_DELAY_MS so a heavily clock-skewed or already-near-expiry
// token doesn't cause a tight refresh loop.
const REFRESH_BUFFER_MS = 60_000;
const MIN_REFRESH_DELAY_MS = 5_000;

export type AuthStatus = "anonymous" | "checking" | "authenticated" | "unauthorized";

// Which workspace an authenticated user should land in. A user holding
// motm_sales_engineer always lands in "se" even if they also hold motm_bd
// -- a proper "pick a workspace" screen for dual-role users is separate,
// not-yet-built frontend work (see the BD build plan's Frontend step).
// "bd"-only users land in BDChatPage; App.tsx routes by this value so an
// SE-role user is never shown BD's screen (or vice versa). "admin" only
// applies to an account with no motm_sales_engineer/motm_bd role (an
// admin who also holds one of those still lands in their normal
// workspace, unaffected) -- see the admin dashboard in App.tsx.
export type Workspace = "se" | "bd" | "admin" | null;

interface AuthContextValue {
  status: AuthStatus;
  workspace: Workspace;
  token: string | null;
  user: MeResponse | null;
  loginError: string | null;
  isSubmittingLogin: boolean;
  signupError: string | null;
  // Set instead of logging the account in -- a self-service signup is
  // created pending admin approval, not authenticated immediately. See
  // signup() below and SignupPage.tsx.
  signupSuccessMessage: string | null;
  isSubmittingSignup: boolean;
  sessionExpired: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (body: SignupRequest) => Promise<void>;
  logout: () => void;
  handleUnauthorized: () => void;
  // Re-fetches /auth/me and applies the result -- used by SettingsPage
  // after a profile save so the header/greeting reflect the new
  // name/username without requiring a full page reload. Workspace routing
  // is re-derived too (harmless no-op for a plain profile edit, since
  // roles don't change here).
  refreshProfile: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function workspaceForRoles(roles: AppRole[]): Workspace {
  if (roles.includes(SALES_ENGINEER_ROLE)) return "se";
  if (roles.includes(BD_ROLE)) return "bd";
  if (roles.includes(ADMIN_ROLE)) return "admin";
  return null;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(TOKEN_STORAGE_KEY));
  const [user, setUser] = useState<MeResponse | null>(null);
  const [workspace, setWorkspace] = useState<Workspace>(null);
  const [status, setStatus] = useState<AuthStatus>(token ? "checking" : "anonymous");
  const [loginError, setLoginError] = useState<string | null>(null);
  const [isSubmittingLogin, setIsSubmittingLogin] = useState(false);
  const [signupError, setSignupError] = useState<string | null>(null);
  const [signupSuccessMessage, setSignupSuccessMessage] = useState<string | null>(null);
  const [isSubmittingSignup, setIsSubmittingSignup] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);
  const refreshTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const applyProfile = useCallback((profile: MeResponse) => {
    setUser(profile);
    const ws = workspaceForRoles(profile.roles);
    setWorkspace(ws);
    setStatus(ws ? "authenticated" : "unauthorized");
  }, []);

  const clearRefreshTimer = useCallback(() => {
    if (refreshTimeoutRef.current !== null) {
      clearTimeout(refreshTimeoutRef.current);
      refreshTimeoutRef.current = null;
    }
  }, []);

  const logout = useCallback(() => {
    clearRefreshTimer();
    localStorage.removeItem(TOKEN_STORAGE_KEY);
    localStorage.removeItem(REFRESH_TOKEN_STORAGE_KEY);
    localStorage.removeItem(EXPIRES_AT_STORAGE_KEY);
    setToken(null);
    setUser(null);
    setWorkspace(null);
    setStatus("anonymous");
  }, [clearRefreshTimer]);

  const handleUnauthorized = useCallback(() => {
    setSessionExpired(true);
    logout();
  }, [logout]);

  // Schedules the next silent refresh a bit before the access token actually
  // expires. Called after every login/signup/refresh, and once on mount for
  // an already-stored session, so the timer survives a page reload.
  const scheduleRefresh = useCallback((expiresAt: number) => {
    clearRefreshTimer();
    const delay = Math.max(expiresAt - Date.now() - REFRESH_BUFFER_MS, MIN_REFRESH_DELAY_MS);
    refreshTimeoutRef.current = setTimeout(() => {
      performRefreshRef.current();
    }, delay);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const persistSession = useCallback(
    ({ access_token, refresh_token, expires_in }: LoginResponse) => {
      const expiresAt = Date.now() + expires_in * 1000;
      localStorage.setItem(TOKEN_STORAGE_KEY, access_token);
      localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, refresh_token);
      localStorage.setItem(EXPIRES_AT_STORAGE_KEY, String(expiresAt));
      setToken(access_token);
      scheduleRefresh(expiresAt);
    },
    [scheduleRefresh],
  );

  // performRefresh reads the refresh_token from storage directly (rather
  // than depending on `token` state) so it always uses whatever was most
  // recently persisted, even when called from a stale closure captured by
  // an old setTimeout. Returns the new access_token on success, or null on
  // failure (after already triggering handleUnauthorized) -- the mount
  // effect below uses the return value to chain a profile fetch; the
  // scheduled-timer call site ignores it.
  const performRefresh = useCallback(async (): Promise<string | null> => {
    const storedRefreshToken = localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY);
    if (!storedRefreshToken) {
      handleUnauthorized();
      return null;
    }
    try {
      const session = await refreshSession({ refresh_token: storedRefreshToken });
      persistSession(session);
      return session.access_token;
    } catch {
      // Refresh token itself is invalid/expired/revoked -- no way to
      // silently recover, fall back to a real re-login.
      handleUnauthorized();
      return null;
    }
  }, [handleUnauthorized, persistSession]);

  // setTimeout callbacks close over whatever `performRefresh` was at
  // schedule time; this ref lets scheduleRefresh always invoke the latest
  // version without needing to re-schedule on every render.
  const performRefreshRef = useRef(performRefresh);
  useEffect(() => {
    performRefreshRef.current = performRefresh;
  }, [performRefresh]);

  const login = useCallback(
    async (email: string, password: string) => {
      setLoginError(null);
      setSessionExpired(false);
      setIsSubmittingLogin(true);
      try {
        const session = await apiLogin({ email, password });
        persistSession(session);
        setStatus("checking");
        const profile = await fetchMe(session.access_token);
        applyProfile(profile);
      } catch (err) {
        if (err instanceof ApiException) {
          if (err.error.kind === "unauthorized") {
            setLoginError("Invalid email or password.");
          } else if (err.error.kind === "network") {
            setLoginError("Couldn't reach the server — check that the backend is running.");
          } else if (err.error.kind === "server") {
            setLoginError("Login service is temporarily unavailable — try again shortly.");
          } else {
            setLoginError(err.message);
          }
        } else {
          setLoginError("Something went wrong logging in.");
        }
        setStatus("anonymous");
        logout();
      } finally {
        setIsSubmittingLogin(false);
      }
    },
    [applyProfile, logout, persistSession],
  );

  const signup = useCallback(async (body: SignupRequest) => {
    setSignupError(null);
    setSignupSuccessMessage(null);
    setSessionExpired(false);
    setIsSubmittingSignup(true);
    try {
      // No session is returned -- a self-service signup is created pending
      // admin approval, not logged in immediately. See client.ts's signup().
      const result = await apiSignup(body);
      setSignupSuccessMessage(result.message);
    } catch (err) {
      if (err instanceof ApiException) {
        if (err.error.kind === "network") {
          setSignupError("Couldn't reach the server — check that the backend is running.");
        } else if (err.error.kind === "server") {
          setSignupError("Signup service is temporarily unavailable — try again shortly.");
        } else if (err.error.kind === "conflict") {
          // Backend's 409 -- almost always "an account with this email
          // already exists" (see POST /auth/signup in app/routers/auth.py).
          setSignupError(err.error.message);
        } else {
          setSignupError(err.message);
        }
      } else {
        setSignupError("Something went wrong creating your account.");
      }
    } finally {
      setIsSubmittingSignup(false);
    }
  }, []);

  // On first mount with a stored token, resolve the profile once. If the
  // stored access token has already expired (e.g. the tab was closed past
  // its lifetime), refresh it first rather than immediately bouncing to
  // login -- the refresh_token typically outlives the access token by a lot
  // longer. On success, schedule the next silent refresh so a page reload
  // doesn't lose the timer that was running before the reload.
  useEffect(() => {
    if (!token || status !== "checking" || user) return;

    const storedExpiresAt = Number(localStorage.getItem(EXPIRES_AT_STORAGE_KEY) ?? 0);
    const isExpired = storedExpiresAt > 0 && storedExpiresAt <= Date.now();

    (async () => {
      let effectiveToken: string | null = token;
      if (isExpired) {
        effectiveToken = await performRefresh();
      }
      if (!effectiveToken) return; // performRefresh already called handleUnauthorized
      try {
        const profile = await fetchMe(effectiveToken);
        applyProfile(profile);
        if (!isExpired && storedExpiresAt > 0) scheduleRefresh(storedExpiresAt);
      } catch {
        logout();
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshProfile = useCallback(async () => {
    if (!token) return;
    const profile = await fetchMe(token);
    applyProfile(profile);
  }, [token, applyProfile]);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      workspace,
      token,
      user,
      loginError,
      isSubmittingLogin,
      signupError,
      signupSuccessMessage,
      isSubmittingSignup,
      sessionExpired,
      login,
      signup,
      logout,
      handleUnauthorized,
      refreshProfile,
    }),
    [
      status,
      workspace,
      token,
      user,
      loginError,
      isSubmittingLogin,
      signupError,
      signupSuccessMessage,
      isSubmittingSignup,
      sessionExpired,
      login,
      signup,
      logout,
      handleUnauthorized,
      refreshProfile,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
