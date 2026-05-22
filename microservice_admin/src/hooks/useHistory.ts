'use client';
import { useCallback, useState } from 'react';

export interface HistoryEntry {
  id: string;
  ts: number;
  time: string;
  action: 'Check' | 'Download' | 'Export' | 'Train' | 'Predict';
  params: Record<string, string>;
  result: string;
  durationMs: number;
}

const STORAGE_KEY = 'modelline:history';
const MAX_ENTRIES = 100;

function loadHistory(): HistoryEntry[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as HistoryEntry[]) : [];
  } catch {
    return [];
  }
}

function saveHistory(entries: HistoryEntry[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  } catch {
    // ignore storage errors
  }
}

export function useHistory() {
  const [history, setHistory] = useState<HistoryEntry[]>(() => loadHistory());

  const addEntry = useCallback(
    (entry: Omit<HistoryEntry, 'id' | 'ts' | 'time'>) => {
      const now = new Date();
      const newEntry: HistoryEntry = {
        ...entry,
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        ts: now.getTime(),
        time: now.toTimeString().slice(0, 8),
      };
      setHistory(prev => {
        const next = [newEntry, ...prev].slice(0, MAX_ENTRIES);
        saveHistory(next);
        return next;
      });
    },
    [],
  );

  const clearHistory = useCallback(() => {
    setHistory([]);
    saveHistory([]);
  }, []);

  return { history, addEntry, clearHistory };
}
