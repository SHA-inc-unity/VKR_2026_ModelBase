import { NextRequest, NextResponse } from 'next/server';

const ACCESS_COOKIE = 'modelline_admin_access_token';

const PUBLIC_PATHS = [
  '/login',
  '/api/auth/login',
  '/api/auth/logout',
  '/api/auth/session',
  '/api/health',
];

export function middleware(req: NextRequest) {
  const pathname = toAppPath(req.nextUrl.pathname, req.nextUrl.basePath);
  if (isPublicPath(pathname) || isStaticPath(pathname)) return NextResponse.next();

  const token = req.cookies.get(ACCESS_COOKIE)?.value;
  if (token && tokenLooksLikeAdmin(token)) return NextResponse.next();

  if (pathname.startsWith('/api/')) {
    return NextResponse.json(
      { error: 'Admin login is required.', code: 'admin_session_required' },
      { status: 401 },
    );
  }

  const loginUrl = new URL(`${req.nextUrl.basePath || ''}/login`, req.url);
  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};

function toAppPath(pathname: string, basePath: string): string {
  if (basePath && pathname.startsWith(basePath)) return pathname.slice(basePath.length) || '/';
  return pathname;
}

function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.some(path => pathname === path || pathname.startsWith(`${path}/`));
}

function isStaticPath(pathname: string): boolean {
  return pathname.startsWith('/_next/') || pathname.startsWith('/public/') || pathname.includes('.');
}

function tokenLooksLikeAdmin(token: string): boolean {
  const payload = decodeJwtPayload(token);
  if (!payload) return false;
  if (typeof payload.exp === 'number' && payload.exp <= Math.floor(Date.now() / 1000)) return false;
  return extractRoles(payload).includes('admin');
}

function decodeJwtPayload(token: string): Record<string, unknown> | null {
  const [, payload] = token.split('.');
  if (!payload) return null;
  try {
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
    return JSON.parse(atob(padded)) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function extractRoles(payload: Record<string, unknown>): string[] {
  const roleClaim = payload.role
    ?? payload.roles
    ?? payload['http://schemas.microsoft.com/ws/2008/06/identity/claims/role'];
  if (Array.isArray(roleClaim)) return roleClaim.map(String).map(role => role.toLowerCase());
  if (typeof roleClaim === 'string') return [roleClaim.toLowerCase()];
  return [];
}