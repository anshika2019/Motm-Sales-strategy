import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiException, fetchMe, login as apiLogin, signup as apiSignup } from "../api/client";
import type { AppRole, MeResponse, SignupRequest } from "../api/types";

const TOKEN_STORAGE_KEY = "motm_access_token";
const SALES_ENGINEER_ROLE: AppRole = "motm_sales_engineer";
const BD_ROLE: AppRole = "motm_bd";

export type AuthStatus = "anonymous" | "checking" | "authenticated" | "unauthorized";

// Which workspace an authenticated user should land in. A user holding
// motm_sales_engineer always lands in "se" even if they also hold motm_bd
// -- a proper "pick a workspace" screen for dual-role users is separate,
// not-yet-built frontend work (see the BD build plan's Frontend step).
// "bd"-only users land in BDChatPage; App.tsx routes by this value so an
// SE-role user is never shown BD's screen (or vice versa).
export type Workspace = "se" | "bd" | null;

interface AuthContextValue {
  status: AuthStatus;
  workspace: Workspace;
  token: string | null;
  user: MeResponse | null;
  loginError: string | null;
  isSubmittingLogin: boolean;
  signupError: string | null;
  isSubmittingSignup: boolean;
  sessionExpired: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (body: SignupRequest) => Promise<void>;
  logout: () => void;
  handleUnauthorized: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function workspaceForRoles(roles: AppRole[]): Workspace {
  if (roles.includes(SALES_ENGINEER_ROLE)) return "se";
  if (roles.includes(BD_ROLE)) return "bd";
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
  const [isSubmittingSignup, setIsSubmittingSignup] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);

  const applyProfile = useCallback((profile: MeResponse) => {
    setUser(profile);
    const ws = workspaceForRoles(profile.roles);
    setWorkspace(ws);
    setStatus(ws ? "authenticated" : "unauthorized");
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
    setToken(null);
    setUser(null);
    setWorkspace(null);
    setStatus("anonymous");
  }, []);

  const handleUnauthorized = useCallback(() => {
    setSessionExpired(true);
    logout();
  }, [logout]);

  const login = useCallback(
    async (email: string, password: string) => {
      setLoginError(null);
      setSessionExpired(false);
      setIsSubmittingLogin(true);
      try {
        const { access_token } = await apiLogin({ email, password });
        localStorage.setItem(TOKEN_STORAGE_KEY, access_token);
        setToken(access_token);
        setStatus("checking");
        const profile = await fetchMe(access_token);
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
        localStorage.removeItem(TOKEN_STORAGE_KEY);
        setToken(null);
      } finally {
        setIsSubmittingLogin(false);
      }
    },
    [applyProfile],
  );

  const signup = useCallback(
    async (body: SignupRequest) => {
      setSignupError(null);
      setSessionExpired(false);
      setIsSubmittingSignup(true);
      try {
        const { access_token } = await apiSignup(body);
        localStorage.setItem(TOKEN_STORAGE_KEY, access_token);
        setToken(access_token);
        setStatus("checking");
        const profile = await fetchMe(access_token);
        applyProfile(profile);
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
        setStatus("anonymous");
        localStorage.removeItem(TOKEN_STORAGE_KEY);
        setToken(null);
      } finally {
        setIsSubmittingSignup(false);
      }
    },
    [applyProfile],
  );

  // On first mount with a stored token, resolve the profile once.
  useEffect(() => {
    if (token && status === "checking" && !user) {
      fetchMe(token)
        .then(applyProfile)
        .catch(() => {
          localStorage.removeItem(TOKEN_STORAGE_KEY);
          setToken(null);
          setStatus("anonymous");
        });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      workspace,
      token,
      user,
      loginError,
      isSubmittingLogin,
      signupError,
      isSubmittingSignup,
      sessionExpired,
      login,
      signup,
      logout,
      handleUnauthorized,
    }),
    [
      status,
      workspace,
      token,
      user,
      loginError,
      isSubmittingLogin,
      signupError,
      isSubmittingSignup,
      sessionExpired,
      login,
      signup,
      logout,
      handleUnauthorized,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
