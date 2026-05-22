import { NextRequest, NextResponse } from 'next/server';
import { clearAdminRuntimeLogs, readAdminRuntimeLogs } from '@/lib/adminRuntimeLog';
import { requireAdminSession } from '@/lib/adminSession';

export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const session = await requireAdminSession(req);
  if (!session.ok) return session.response;

  const limitParam = req.nextUrl.searchParams.get('limit');
  const limit = Math.min(Math.max(Number(limitParam) || 200, 1), 500);
  return NextResponse.json({ logs: readAdminRuntimeLogs(limit) });
}

export async function DELETE(req: NextRequest) {
  const session = await requireAdminSession(req);
  if (!session.ok) return session.response;

  clearAdminRuntimeLogs();
  return NextResponse.json({ ok: true });
}