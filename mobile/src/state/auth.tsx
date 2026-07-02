/**
 * Auth + session context. Bootstraps from a stored rpg_session cookie by hitting
 * /api/v1/auth/me; exposes login/register/logout and the current server URL.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { auth as authApi, User } from "@/api";
import { setAuthExpiredHandler } from "@/api/http";
import {
  getServerUrl,
  setServerUrl as persistServerUrl,
  setSessionCookie,
} from "@/api/storage";

type AuthState = {
  ready: boolean;
  user: User | null;
  serverUrl: string | null;
  setServer: (url: string) => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  register: (body: Record<string, unknown>) => Promise<{ pending: boolean }>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<User | null>(null);
  const [serverUrl, setServerUrl] = useState<string | null>(null);
  const bootedRef = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const r = await authApi.me();
      setUser(r?.user ?? null);
    } catch {
      setUser(null);
    }
  }, []);

  useEffect(() => {
    // Defer expired-handler registration until boot completes, preventing a race
    // where a 401 during initial /me wipes a valid session.
    (async () => {
      const url = await getServerUrl();
      setServerUrl(url);
      if (url) await refresh();
      bootedRef.current = true;
      setAuthExpiredHandler(() => {
        if (bootedRef.current) setUser(null);
      });
      setReady(true);
    })();
    return () => {
      bootedRef.current = false;
      setAuthExpiredHandler(null);
    };
  }, [refresh]);

  const setServer = useCallback(async (url: string) => {
    await persistServerUrl(url);
    setServerUrl(url);
    setUser(null);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const r = await authApi.login(username, password);
    setUser(r?.user ?? null);
  }, []);

  const register = useCallback(async (body: Record<string, unknown>) => {
    const r = await authApi.register(body);
    if (r?.user && (r.auto_verified || !r.pending_verify)) {
      setUser(r.user);
      return { pending: false };
    }
    return { pending: !!r?.pending_verify };
  }, []);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      /* best effort */
    }
    await setSessionCookie(null);
    await persistServerUrl("");
    setUser(null);
    setServerUrl(null);
  }, []);

  const value = useMemo(
    () => ({ ready, user, serverUrl, setServer, login, register, logout, refresh }),
    [ready, user, serverUrl, setServer, login, register, logout, refresh],
  );

  return (
    <Ctx.Provider value={value}>
      {children}
    </Ctx.Provider>
  );
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth must be used within AuthProvider");
  return v;
}
