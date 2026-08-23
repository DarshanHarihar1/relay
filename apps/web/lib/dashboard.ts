"use client";

import { useCallback, useEffect, useState } from "react";

import type { DashboardView } from "../../../packages/contracts/src";

import { getDashboard } from "./relay-api";

export type DashboardResult = {
  data: DashboardView | null;
  error: Error | null;
  isLoading: boolean;
  isOffline: boolean;
  refresh: () => void;
};

export function useDashboard(enabled: boolean): DashboardResult {
  const [data, setData] = useState<DashboardView | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [isLoading, setIsLoading] = useState(enabled);
  const [refreshVersion, setRefreshVersion] = useState(0);

  const refresh = useCallback(() => setRefreshVersion((version) => version + 1), []);

  useEffect(() => {
    if (!enabled) {
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    getDashboard()
      .then((next) => {
        if (!cancelled) {
          setData(next);
          setError(null);
          setIsLoading(false);
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(caught instanceof Error ? caught : new Error("Relay is offline."));
          setIsLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, refreshVersion]);

  return { data, error, isLoading, isOffline: error !== null, refresh };
}
