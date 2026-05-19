import { NextRequest, NextResponse } from 'next/server';
import { clearAdminRuntimeLogs, readAdminRuntimeLogs } from '@/lib/adminRuntimeLog';

export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const limitParam = req.nextUrl.searchParams.get('limit');
  const limit = Math.min(Math.max(Number(limitParam) || 200, 1), 500);
  return NextResponse.json({ logs: readAdminRuntimeLogs(limit) });
}

export async function DELETE() {
  clearAdminRuntimeLogs();
  return NextResponse.json({ ok: true });
}