import 'server-only';
import Redis from 'ioredis';

// ── Singleton client ──────────────────────────────────────────────────────────

let _client: Redis | null = null;

function getClient(): Redis | null {
  if (_client) return _client;

  const url = process.env.REDIS_URL;
  if (!url) return null;

  try {
    _client = new Redis(url, {
      // Fail fast: don't queue commands when Redis is unreachable
      lazyConnect: true,
      enableOfflineQueue: false,
      connectTimeout: 2000,
      commandTimeout: 1000,
      maxRetriesPerRequest: 0,
      retryStrategy: () => null, // no auto-retry
    });

    _client.on('error', () => {
      /* suppress — Redis being down must never crash the app */
    });
  } catch {
    _client = null;
  }

  return _client;
}

// ── Public API ────────────────────────────────────────────────────────────────

export async function cacheGet(key: string): Promise<string | null> {
  try {
    const client = getClient();
    if (!client) return null;
    return await client.get(key);
  } catch {
    return null;
  }
}

export async function cacheSet(key: string, value: string, ttlSeconds: number): Promise<void> {
  try {
    const client = getClient();
    if (!client) return;
    await client.set(key, value, 'EX', ttlSeconds);
  } catch {
    /* ignore */
  }
}
