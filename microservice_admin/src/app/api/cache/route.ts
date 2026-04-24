import { NextRequest, NextResponse } from 'next/server';
import { cacheGet, cacheSet } from '@/lib/redisCache';

export async function GET(req: NextRequest) {
  const key = req.nextUrl.searchParams.get('key');
  if (!key) {
    return NextResponse.json({ error: 'Missing key' }, { status: 400 });
  }
  const value = await cacheGet(key);
  return NextResponse.json({ value });
}

export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  const { key, value, ttl } = body as { key?: unknown; value?: unknown; ttl?: unknown };

  if (typeof key !== 'string' || !key) {
    return NextResponse.json({ error: 'Missing key' }, { status: 400 });
  }
  if (typeof value !== 'string') {
    return NextResponse.json({ error: 'value must be a string' }, { status: 400 });
  }
  const ttlSeconds = typeof ttl === 'number' && ttl > 0 ? ttl : 3600;

  await cacheSet(key, value, ttlSeconds);
  return NextResponse.json({ ok: true });
}
