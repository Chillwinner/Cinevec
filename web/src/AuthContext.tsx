import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, setUnauthorizedHandler } from "./api";
import { clearToken, readToken, shouldRedirectToLogin, storeFromLogin, tokenExpired } from "./authStorage";
import type { AuthPublicStatus } from "./types";

type AuthContextValue = {
  auth: AuthPublicStatus | null;
  username: string | null;
  ready: boolean;
  refresh: () => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  rememberToken: (body: { token: string; username?: string; expires_in?: number }) => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [auth, setAuth] = useState<AuthPublicStatus | null>(null);
  const [username, setUsername] = useState<string | null>(readToken()?.username ?? null);
  const [ready, setReady] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  const logout = useCallback(() => {
    clearToken();
    setUsername(null);
    if (auth?.enabled) navigate("/login", { replace: true, state: { from: location.pathname } });
  }, [auth?.enabled, location.pathname, navigate]);

  const refresh = useCallback(async () => {
    const status = await api.authStatus();
    setAuth(status);
    const stored = readToken();
    if (status.enabled && tokenExpired(stored)) {
      clearToken();
      setUsername(null);
    } else if (stored?.username) {
      setUsername(stored.username);
    }
    setReady(true);
  }, []);

  useEffect(() => {
    void refresh().catch(() => setReady(true));
  }, [refresh]);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      clearToken();
      setUsername(null);
      if (window.location.pathname !== "/login") {
        window.location.assign("/login");
      }
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  useEffect(() => {
    if (!ready || !auth) return;
    const stored = readToken();
    if (shouldRedirectToLogin(auth.enabled, 200, stored) && location.pathname !== "/login") {
      navigate("/login", { replace: true, state: { from: location.pathname } });
    }
    if (!auth.enabled && location.pathname === "/login") {
      navigate("/settings", { replace: true });
    }
  }, [auth, location.pathname, navigate, ready]);

  const login = useCallback(async (name: string, password: string) => {
    const body = await api.login(name, password);
    storeFromLogin(body);
    setUsername(body.username);
    await refresh();
  }, [refresh]);

  const rememberToken = useCallback((body: { token: string; username?: string; expires_in?: number }) => {
    storeFromLogin(body);
    setUsername(body.username ?? null);
  }, []);

  const value = useMemo(
    () => ({ auth, username, ready, refresh, login, logout, rememberToken }),
    [auth, username, ready, refresh, login, logout, rememberToken],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("AuthProvider missing");
  return ctx;
}
