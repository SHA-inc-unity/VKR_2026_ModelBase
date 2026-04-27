/**
 * Server-only short-TTL coalescing cache for Kafka request/reply.
 *
 * Several admin pages mount at the same time and each fires the same
 * read-only Kafka request (health, list_tables, coverage). Without
 * coalescing every component triggers an independent roundtrip and a
 * hot dashboard load can produce 30+ Kafka messages in a 1-second window.
 *
 * `coalesce(key, ttl, factory)` returns the same in-flight promise to
 * concurrent callers and caches the resolved value for `ttlMs`. The cache
 * is per-process and is intentionally tiny (Map, no LRU): the keys are
 * limited to a handful of read-only summary topics enabled via the
 * `coalescePolicy` allow-list below.
 *
 * Mutating commands (ingest, delete, clean, import, train, anomaly run)
 * never coalesce — they pass through the proxy as-is.
 */

interface CacheEntry {
  expiresAt: number;
  value: Record<string, unknown>;
}
interface InFlightEntry {
  promise: Promise<Record<string, unknown>>;
}

const cache    = new Map<string, CacheEntry>();
const inFlight = new Map<string, InFlightEntry>();

/** Default TTL per topic. Topics not listed here are not coalesced. */
const COALESCE_TTL_MS: Record<string, number> = {
  // Health / ping — small, very hot, can tolerate a 1 s lag
  'cmd.data.health':            1_500,
  'cmd.analytics.health':       1_500,
  'cmd.data.db.ping':           1_500,
  // Catalogue — refreshed at most every couple of seconds is plenty for UI
  'cmd.data.dataset.list_tables':      2_000,
  'cmd.data.dataset.coverage':         2_000,
  'cmd.data.dataset.constants':       30_000,
  'cmd.data.dataset.table_schema':    10_000,
  // Analitic session metadata — same as coverage
  'cmd.analitic.dataset.status':       1_500,
  // Models list — read-only registry, refreshed by EVT_ANALYTICS_MODEL_READY
  'cmd.analytics.model.list':          5_000,
};

export function coalesceTtlFor(topic: string, payload: unknown): number | null {
  const ttl = COALESCE_TTL_MS[topic];
  if (ttl === undefined) return null;
  // Skip coalescing if caller passed a correlationId — they probably want
  // their own progress events to fire fresh.
  if (payload && typeof payload === 'object' && 'correlationId' in (payload as Record<string, unknown>)) {
    return null;
  }
  return ttl;
}

export function makeKey(topic: string, payload: Record<string, unknown> | null | undefined): string {
  // Stable JSON: sort keys so {a:1,b:2} and {b:2,a:1} map to the same entry.
  const stable = stableStringify(payload ?? {});
  return `${topic}|${stable}`;
}

function stableStringify(v: unknown): string {
  if (v === null || typeof v !== 'object') return JSON.stringify(v);
  if (Array.isArray(v)) return `[${v.map(stableStringify).join(',')}]`;
  const keys = Object.keys(v as Record<string, unknown>).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${stableStringify((v as Record<string, unknown>)[k])}`).join(',')}}`;
}

export async function coalesce(
  key: string,
  ttlMs: number,
  factory: () => Promise<Record<string, unknown>>,
): Promise<Record<string, unknown>> {
  const now = Date.now();

  const cached = cache.get(key);
  if (cached && cached.expiresAt > now) return cached.value;

  const inflight = inFlight.get(key);
  if (inflight) return inflight.promise;

  const promise = factory()
    .then((value) => {
      cache.set(key, { value, expiresAt: Date.now() + ttlMs });
      return value;
    })
    .finally(() => {
      inFlight.delete(key);
    });

  inFlight.set(key, { promise });
  return promise;
}

/** Test/diagnostic helper. */
export function _coalesceStats(): { cached: number; inFlight: number } {
  const now = Date.now();
  // GC stale entries opportunistically.
  for (const [k, e] of cache) if (e.expiresAt <= now) cache.delete(k);
  return { cached: cache.size, inFlight: inFlight.size };
}
