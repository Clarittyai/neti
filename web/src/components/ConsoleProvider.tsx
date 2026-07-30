"use client";

/**
 * One place that knows whether the gate is reachable, connected, and enforcing.
 *
 * Deliberately small: a context holding `state` plus a refresh, and nothing else. Screens fetch
 * their own data. A store that owned every screen's data would need cache invalidation, and the
 * only thing genuinely shared here is the answer to "what am I connected to".
 */

import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { ApiError, api, type ConsoleState, type Mode } from "@/lib/api";

interface Value {
  state: ConsoleState | null;
  error: string | null;
  loading: boolean;
  refresh: () => Promise<void>;
  connect: () => Promise<void>;
  setMode: (mode: Mode) => Promise<void>;
}

const Ctx = createContext<Value | null>(null);

export function ConsoleProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<ConsoleState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setState(await api.state());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const connect = useCallback(async () => {
    await api.connect();
    await refresh();
  }, [refresh]);

  const setMode = useCallback(
    async (mode: Mode) => {
      await api.setMode(mode);
      await refresh();
    },
    [refresh],
  );

  return (
    <Ctx.Provider value={{ state, error, loading, refresh, connect, setMode }}>
      {children}
    </Ctx.Provider>
  );
}

export function useConsole(): Value {
  const value = useContext(Ctx);
  if (!value) throw new Error("useConsole must be used inside <ConsoleProvider>");
  return value;
}
