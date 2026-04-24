/**
 * Browser-safe cache client. Communicates with /api/cache.
 * Never imports ioredis or any server-only module.
 */

function apiBase(): string {
  return process.env.NEXT_PUBLIC_BASE_PATH ?? '';
}

export async function cacheRead<T>(key: string): Promise<T | null> {
  try {
    const res = await fetch(`${apiBase()}/api/cache?key=${encodeURIComponent(key)}`);
    if (!res.ok) return null;
    const json = await res.json() as { value: string | null };
    if (json.value == null) return null;
    return JSON.parse(json.value) as T;
  } catch {
    return null;
  }
}

export async function cacheWrite(key: string, value: unknown, ttl: number): Promise<void> {
  try {
    await fetch(`${apiBase()}/api/cache`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, value: JSON.stringify(value), ttl }),
    });
  } catch {
    /* ignore */
  }
}
