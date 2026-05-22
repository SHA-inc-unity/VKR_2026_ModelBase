import { NextRequest, NextResponse } from 'next/server';
import { appendQueueHistory, clearQueueHistory, readQueueHistory } from '@/lib/queueHistoryStore';
import { requireAdminSession } from '@/lib/adminSession';

export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const session = await requireAdminSession(req);
  if (!session.ok) return session.response;

  const limitParam = req.nextUrl.searchParams.get('limit');
  const limit = Math.min(Math.max(Number(limitParam) || 200, 1), 400);
  return NextResponse.json({ items: await readQueueHistory(limit) });
}

export async function DELETE(req: NextRequest) {
  const session = await requireAdminSession(req);
  if (!session.ok) return session.response;

  await clearQueueHistory();
  return NextResponse.json({ ok: true });
}

export async function POST(req: NextRequest) {
  const session = await requireAdminSession(req);
  if (!session.ok) return session.response;

  const body = await req.json().catch(() => null) as Record<string, unknown> | null;
  const id = typeof body?.id === 'string' ? body.id.trim() : '';
  const topic = typeof body?.topic === 'string' ? body.topic.trim() : '';
  const level = body?.level === 'success' || body?.level === 'error' ? body.level : null;

  if (!id || !topic || !level) {
    return NextResponse.json({ error: 'id, topic and level are required' }, { status: 400 });
  }

  await appendQueueHistory({
    id,
    ts: typeof body?.ts === 'string' && body.ts ? body.ts : new Date().toISOString(),
    topic,
    level,
    durationMs: typeof body?.durationMs === 'number' && Number.isFinite(body.durationMs) ? body.durationMs : 0,
    splitMode: Boolean((process.env.ADMIN_BACKEND_BASE_URL ?? '').trim()),
    payloadSummary: body?.payloadSummary && typeof body.payloadSummary === 'object' && !Array.isArray(body.payloadSummary)
      ? body.payloadSummary as Record<string, unknown>
      : null,
    responseSummary: body?.responseSummary && typeof body.responseSummary === 'object' && !Array.isArray(body.responseSummary)
      ? body.responseSummary as Record<string, unknown>
      : null,
    message: typeof body?.message === 'string' ? body.message : null,
    code: typeof body?.code === 'string' ? body.code : null,
    detail: typeof body?.detail === 'string' ? body.detail : null,
    correlationId: typeof body?.correlationId === 'string' ? body.correlationId : null,
  });

  return NextResponse.json({ ok: true });
}