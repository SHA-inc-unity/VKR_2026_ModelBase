import { NextRequest, NextResponse } from 'next/server';

const ACCESS_COOKIE = 'modelline_admin_access_token';
const REFRESH_COOKIE = 'modelline_admin_refresh_token';
const ACCOUNT_URL = normalizeBaseUrl(process.env.ACCOUNT_URL ?? 'account_service_api:5000');
const ADMIN_ROLE = 'admin';
const SECURE_COOKIES = process.env.NODE_ENV === 'production';

const PUBLIC_PATHS = [
  '/login',
  '/api/auth/login',
  '/api/auth/logout',
  '/api/health',
];

type AuthUser = {
  roles?: string[];
};

type AuthResponse = {
  accessToken?: string;
  refreshToken?: string;
  accessTokenExpiresAt?: string;
  refreshTokenExpiresAt?: string;
  accountType?: string;
  roles?: string[];
  user?: AuthUser;
};

export async function middleware(req: NextRequest) {
  const pathname = toAppPath(req.nextUrl.pathname, req.nextUrl.basePath);
  if (isStaticPath(pathname)) return NextResponse.next();

  const isLoginPage = pathname === '/login';
  const isPublicPathname = isPublicPath(pathname);

  const token = req.cookies.get(ACCESS_COOKIE)?.value;
  if (token && tokenLooksLikeAdmin(token)) {
    return isLoginPage ? redirectHome(req) : NextResponse.next();
  }

  const refreshToken = req.cookies.get(REFRESH_COOKIE)?.value;
  if (refreshToken) {
    const refreshedAuth = await refreshAdminSession(refreshToken);
    if (refreshedAuth) {
      if (isLoginPage) {
        const response = redirectHome(req);
        setAdminAuthCookies(response, refreshedAuth);
        return response;
      }

      const requestHeaders = new Headers(req.headers);
      requestHeaders.set('cookie', buildCookieHeader(req, refreshedAuth));
      const response = NextResponse.next({ request: { headers: requestHeaders } });
      setAdminAuthCookies(response, refreshedAuth);
      return response;
    }

    const response = isPublicPathname ? NextResponse.next() : unauthorizedResponse(req, pathname);
    clearAdminAuthCookies(response);
    return response;
  }

  if (isPublicPathname) {
    return NextResponse.next();
  }

  return unauthorizedResponse(req, pathname);
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

function redirectHome(req: NextRequest): NextResponse {
  const homeUrl = new URL(req.nextUrl.basePath ? `${req.nextUrl.basePath}/` : '/', req.url);
  return NextResponse.redirect(homeUrl);
}

function unauthorizedResponse(req: NextRequest, pathname: string): NextResponse {
  if (pathname.startsWith('/api/')) {
    return NextResponse.json(
      { error: 'Admin login is required.', code: 'admin_session_required' },
      { status: 401 },
    );
  }

  const loginUrl = new URL(`${req.nextUrl.basePath || ''}/login`, req.url);
  return NextResponse.redirect(loginUrl);
}

function normalizeBaseUrl(value: string): string {
  const trimmed = value.trim().replace(/\/$/, '');
  if (!trimmed) return '';
  return /^https?:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`;
}

async function refreshAdminSession(refreshToken: string): Promise<AuthResponse | null> {
  try {
    const res = await fetch(`${ACCOUNT_URL}/api/account/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refreshToken }),
      cache: 'no-store',
    });

    const raw = await res.text();
    const body = raw ? JSON.parse(raw) as AuthResponse : {};
    if (!res.ok) return null;

    const roles = collectAuthRoles(body);
    if (!body.accessToken || !body.refreshToken || !roles.includes(ADMIN_ROLE)) {
      return null;
    }

    return body;
  } catch {
    return null;
  }
}

function setAdminAuthCookies(response: NextResponse, auth: AuthResponse): void {
  if (!auth.accessToken || !auth.refreshToken) return;

  const accessExpires = auth.accessTokenExpiresAt ? new Date(auth.accessTokenExpiresAt) : undefined;
  const refreshExpires = auth.refreshTokenExpiresAt ? new Date(auth.refreshTokenExpiresAt) : undefined;

  response.cookies.set(ACCESS_COOKIE, auth.accessToken, {
    httpOnly: true,
    secure: SECURE_COOKIES,
    sameSite: 'strict',
    path: '/',
    expires: accessExpires,
  });
  response.cookies.set(REFRESH_COOKIE, auth.refreshToken, {
    httpOnly: true,
    secure: SECURE_COOKIES,
    sameSite: 'strict',
    path: '/',
    expires: refreshExpires,
  });
}

function clearAdminAuthCookies(response: NextResponse): void {
  response.cookies.set(ACCESS_COOKIE, '', {
    httpOnly: true,
    secure: SECURE_COOKIES,
    sameSite: 'strict',
    path: '/',
    maxAge: 0,
  });
  response.cookies.set(REFRESH_COOKIE, '', {
    httpOnly: true,
    secure: SECURE_COOKIES,
    sameSite: 'strict',
    path: '/',
    maxAge: 0,
  });
}

function buildCookieHeader(req: NextRequest, auth: AuthResponse): string {
  const cookies = new Map(req.cookies.getAll().map(cookie => [cookie.name, cookie.value]));
  if (auth.accessToken) cookies.set(ACCESS_COOKIE, auth.accessToken);
  if (auth.refreshToken) cookies.set(REFRESH_COOKIE, auth.refreshToken);

  return Array.from(cookies.entries())
    .map(([name, value]) => `${name}=${encodeURIComponent(value)}`)
    .join('; ');
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

function collectAuthRoles(auth: AuthResponse): string[] {
  return [...new Set([
    ...(auth.roles ?? []),
    ...(auth.user?.roles ?? []),
    ...(auth.accountType ? [auth.accountType] : []),
  ].map(role => role.toLowerCase()))];
}