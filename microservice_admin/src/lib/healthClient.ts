/**
 * Client-side helper to call the /api/health probe route.
 */
import type { InfraHealthResponse } from './types';

export async function fetchInfraHealth(): Promise<InfraHealthResponse> {
  const base = process.env.NEXT_PUBLIC_BASE_PATH ?? '';
  const res = await fetch(`${base}/api/health`, { cache: 'no-store' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as InfraHealthResponse;
}
