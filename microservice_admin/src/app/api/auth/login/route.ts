import { NextRequest, NextResponse } from 'next/server';
import { AdminLoginForbiddenError, loginAdmin, setAdminAuthCookies } from '@/lib/adminSession';

export const dynamic = 'force-dynamic';

export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  const { email, password } = body as { email?: unknown; password?: unknown };
  if (typeof email !== 'string' || typeof password !== 'string') {
    return NextResponse.json({ error: 'Email and password are required' }, { status: 400 });
  }

  try {
    const auth = await loginAdmin(email, password);
    const response = NextResponse.json({
      ok: true,
      uid: auth.uid ?? auth.user?.id,
      accountType: auth.accountType ?? 'admin',
      roles: auth.roles ?? auth.user?.roles ?? ['admin'],
      user: auth.user,
    });
    setAdminAuthCookies(response, auth);
    return response;
  } catch (err) {
    const status = err instanceof AdminLoginForbiddenError ? 403 : 401;
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: message }, { status });
  }
}