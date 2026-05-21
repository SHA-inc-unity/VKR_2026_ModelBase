import { NextResponse } from 'next/server';
import { clearAdminAuthCookies } from '@/lib/adminSession';

export const dynamic = 'force-dynamic';

export async function POST() {
  const response = NextResponse.json({ ok: true });
  clearAdminAuthCookies(response);
  return response;
}