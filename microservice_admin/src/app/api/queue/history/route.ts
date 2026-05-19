import { NextRequest, NextResponse } from 'next/server';
import { clearQueueHistory, readQueueHistory } from '@/lib/queueHistoryStore';

export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const limitParam = req.nextUrl.searchParams.get('limit');
  const limit = Math.min(Math.max(Number(limitParam) || 200, 1), 400);
  return NextResponse.json({ items: await readQueueHistory(limit) });
}

export async function DELETE() {
  await clearQueueHistory();
  return NextResponse.json({ ok: true });
}