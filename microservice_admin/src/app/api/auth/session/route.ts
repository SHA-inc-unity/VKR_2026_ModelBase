import { NextRequest, NextResponse } from 'next/server';
import { requireAdminSession } from '@/lib/adminSession';
import { isSplitMode } from '@/lib/backendClient';

export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const session = await requireAdminSession(req, { verifyWithAccount: !isSplitMode });
  if (!session.ok) return session.response;
  return NextResponse.json({ ok: true, user: session.user });
}