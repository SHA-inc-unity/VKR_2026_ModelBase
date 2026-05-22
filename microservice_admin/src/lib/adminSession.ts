import 'server-only';

import { NextRequest, NextResponse } from 'next/server';

export const ADMIN_ACCESS_COOKIE = 'modelline_admin_access_token';
export const ADMIN_REFRESH_COOKIE = 'modelline_admin_refresh_token';

const ACCOUNT_URL = normalizeBaseUrl(process.env.ACCOUNT_URL ?? 'account_service_api:5000');
const ADMIN_ROLE = 'admin';
const SECURE_COOKIES = process.env.NODE_ENV === 'production';

type AuthUser = {
  id?: string;
  uid?: string;
  email?: string;
  username?: string;
  roles?: string[];
};

type AuthResponse = {
  accessToken?: string;
  refreshToken?: string;
  accessTokenExpiresAt?: string;
  refreshTokenExpiresAt?: string;
  uid?: string;
  accountType?: string;
  roles?: string[];
  user?: AuthUser;
};

type SessionResult =
  | { ok: true; accessToken: string; user: AuthUser | null }
  | { ok: false; response: NextResponse };

export function normalizeBaseUrl(value: string): string {
  const trimmed = value.trim().replace(/\/$/, '');
  if (!trimmed) return '';
  return /^https?:\/\//i.test(trimmed) ? trimmed : `http://${trimmed}`;
}

export function readAdminAccessToken(req: NextRequest): string | null {
  return req.cookies.get(ADMIN_ACCESS_COOKIE)?.value ?? null;
}

export function setAdminAuthCookies(response: NextResponse, auth: AuthResponse): void {
  if (!auth.accessToken || !auth.refreshToken) return;

  const accessExpires = auth.accessTokenExpiresAt ? new Date(auth.accessTokenExpiresAt) : undefined;
  const refreshExpires = auth.refreshTokenExpiresAt ? new Date(auth.refreshTokenExpiresAt) : undefined;

  response.cookies.set(ADMIN_ACCESS_COOKIE, auth.accessToken, {
    httpOnly: true,
    secure: SECURE_COOKIES,
    sameSite: 'strict',
    path: '/',
    expires: accessExpires,
  });
  response.cookies.set(ADMIN_REFRESH_COOKIE, auth.refreshToken, {
    httpOnly: true,
    secure: SECURE_COOKIES,
    sameSite: 'strict',
    path: '/',
    expires: refreshExpires,
  });
}

export function clearAdminAuthCookies(response: NextResponse): void {
  response.cookies.set(ADMIN_ACCESS_COOKIE, '', { httpOnly: true, secure: SECURE_COOKIES, sameSite: 'strict', path: '/', maxAge: 0 });
  response.cookies.set(ADMIN_REFRESH_COOKIE, '', { httpOnly: true, secure: SECURE_COOKIES, sameSite: 'strict', path: '/', maxAge: 0 });
}

export function tokenLooksLikeAdmin(accessToken: string): boolean {
  const payload = decodeJwtPayload(accessToken);
  if (!payload) return false;

  if (typeof payload.exp === 'number' && payload.exp <= Math.floor(Date.now() / 1000)) return false;
  return extractRoles(payload).includes(ADMIN_ROLE);
}

export async function loginAdmin(login: string, password: string): Promise<AuthResponse> {
  const res = await fetch(`${ACCOUNT_URL}/api/account/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: login, password }),
    signal: AbortSignal.timeout(10_000),
  });

  const raw = await res.text();
  const body = raw ? JSON.parse(raw) as AuthResponse & { error?: string; detail?: string } : {};

  if (!res.ok) {
    throw new Error(body.detail || body.error || `Account login failed with HTTP ${res.status}`);
  }

  const roles = collectAuthRoles(body);
  if (!roles.includes(ADMIN_ROLE)) {
    throw new AdminLoginForbiddenError();
  }

  if (!body.accessToken || !body.refreshToken) {
    throw new Error('Account login response is missing tokens');
  }

  return body;
}

export async function requireAdminSession(
  req: NextRequest,
  options: { verifyWithAccount?: boolean } = {},
): Promise<SessionResult> {
  const accessToken = readAdminAccessToken(req);
  if (!accessToken || !tokenLooksLikeAdmin(accessToken)) {
    return unauthorized('admin_session_required', 'Admin login is required.');
  }

  if (options.verifyWithAccount) {
    const user = await fetchCurrentAdmin(accessToken);
    if (!user || !collectUserRoles(user).includes(ADMIN_ROLE)) {
      return unauthorized('admin_session_invalid', 'Admin session is invalid or expired.');
    }
    return { ok: true, accessToken, user };
  }

  return { ok: true, accessToken, user: null };
}

async function fetchCurrentAdmin(accessToken: string): Promise<AuthUser | null> {
  try {
    const res = await fetch(`${ACCOUNT_URL}/api/account/me`, {
      headers: { Authorization: `Bearer ${accessToken}` },
      signal: AbortSignal.timeout(5_000),
      cache: 'no-store',
    });
    if (!res.ok) return null;
    const body = await res.json() as AuthUser;
    return body;
  } catch {
    return null;
  }
}

function unauthorized(code: string, detail: string): SessionResult {
  return {
    ok: false,
    response: NextResponse.json({ error: detail, code, detail }, { status: 401 }),
  };
}

function decodeJwtPayload(token: string): Record<string, unknown> | null {
  const [, payload] = token.split('.');
  if (!payload) return null;
  try {
    const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
    return JSON.parse(Buffer.from(padded, 'base64').toString('utf8')) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function collectAuthRoles(auth: AuthResponse): string[] {
  return [...new Set([
    ...(auth.roles ?? []),
    ...collectUserRoles(auth.user ?? null),
    ...(auth.accountType ? [auth.accountType] : []),
  ].map(role => role.toLowerCase()))];
}

function collectUserRoles(user: AuthUser | null): string[] {
  return (user?.roles ?? []).map(role => role.toLowerCase());
}

function extractRoles(payload: Record<string, unknown>): string[] {
  const roleClaim = payload.role
    ?? payload.roles
    ?? payload['http://schemas.microsoft.com/ws/2008/06/identity/claims/role'];
  if (Array.isArray(roleClaim)) return roleClaim.map(String).map(role => role.toLowerCase());
  if (typeof roleClaim === 'string') return [roleClaim.toLowerCase()];
  return [];
}

export class AdminLoginForbiddenError extends Error {
  constructor() {
    super('Only admin accounts can sign in to the admin panel.');
    this.name = 'AdminLoginForbiddenError';
  }
}