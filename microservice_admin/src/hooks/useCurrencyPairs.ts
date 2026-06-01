'use client';

/**
 * Currency Pairs Center — the single client-side source of truth for the
 * platform's trading pairs. Reads/mutates the data-service `currency_pair_assets`
 * registry over Kafka (cmd.data.pairs.*). Active symbols = active base × active
 * quote cross-product (computed by the data service). A module-level cache +
 * listener set means every page that calls these hooks shares one in-memory
 * copy and updates together after any mutation. No pair list is hardcoded in
 * the admin anymore.
 */

import { useCallback, useEffect, useState } from 'react';
import { kafkaCall } from '@/lib/kafkaClient';
import { Topics } from '@/lib/topics';

export type PairRole = 'base' | 'quote';

export interface PairAsset {
  asset: string;
  active: boolean;
}

export interface PairsState {
  bases: PairAsset[];
  quotes: PairAsset[];
  symbols: string[];
}

const EMPTY: PairsState = { bases: [], quotes: [], symbols: [] };

let cache: PairsState | null = null;
let inflight: Promise<void> | null = null;
const listeners = new Set<(s: PairsState) => void>();

function normalize(data: Partial<PairsState> | null | undefined): PairsState {
  return {
    bases: data?.bases ?? [],
    quotes: data?.quotes ?? [],
    symbols: data?.symbols ?? [],
  };
}

function broadcast(s: PairsState) {
  cache = s;
  listeners.forEach(l => l(s));
}

async function fetchPairs(): Promise<void> {
  // De-dupe concurrent first-loads from multiple mounted hooks.
  if (inflight) return inflight;
  inflight = (async () => {
    try {
      const data = await kafkaCall<PairsState>(Topics.CMD_DATA_PAIRS_LIST, {}, 15_000);
      broadcast(normalize(data));
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

export function useCurrencyPairs() {
  const [state, setState] = useState<PairsState>(cache ?? EMPTY);
  const [loading, setLoading] = useState<boolean>(cache === null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(cache === null);
    try {
      await fetchPairs();
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const l = (s: PairsState) => setState(s);
    listeners.add(l);
    if (cache !== null) setState(cache);
    void refresh();
    return () => { listeners.delete(l); };
  }, [refresh]);

  const mutate = useCallback(async (topic: string, payload: Record<string, unknown>) => {
    const data = await kafkaCall<PairsState & { error?: string }>(topic, payload, 15_000);
    if (data && (data as { error?: string }).error) {
      throw new Error((data as { error?: string }).error);
    }
    broadcast(normalize(data));
  }, []);

  const addAsset = useCallback(
    (role: PairRole, asset: string) => mutate(Topics.CMD_DATA_PAIRS_ADD, { role, asset }),
    [mutate]);
  const removeAsset = useCallback(
    (role: PairRole, asset: string) => mutate(Topics.CMD_DATA_PAIRS_REMOVE, { role, asset }),
    [mutate]);
  const setActive = useCallback(
    (role: PairRole, asset: string, active: boolean) =>
      mutate(Topics.CMD_DATA_PAIRS_SET_ACTIVE, { role, asset, active }),
    [mutate]);

  return { ...state, loading, error, refresh, addAsset, removeAsset, setActive };
}

/**
 * Convenience hook for consumer dropdowns (download/train/compare/anomaly):
 * just the active center symbols, plus an 'ALL' sentinel variant.
 */
export function useSymbols() {
  const { symbols, loading, refresh } = useCurrencyPairs();
  return { symbols, symbolsAll: ['ALL', ...symbols], loading, refresh };
}
