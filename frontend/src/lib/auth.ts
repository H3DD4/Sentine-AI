/**
 * Sentinel.AI — Authentication Store
 *
 * Handles JWT access + refresh tokens, persisted in localStorage.
 * All API fetch calls go through `apiFetch()` which auto-injects the
 * Authorization header and retries once on 401 using the refresh token.
 */

const ACCESS_KEY = "sentinel.auth.access";
const REFRESH_KEY = "sentinel.auth.refresh";
const USER_KEY = "sentinel.auth.user";

const API_BASE =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ??
  "http://localhost:8002";

export interface AuthUser {
  id: string;
  username: string;
  email: string;
  is_active: boolean;
}

// ── Token storage ──────────────────────────────────────────────────────────

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACCESS_KEY);
}

export function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(REFRESH_KEY);
}

export function getStoredUser(): AuthUser | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as AuthUser) : null;
  } catch {
    return null;
  }
}

export function isAuthenticated(): boolean {
  const token = getAccessToken();
  if (!token) return false;
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))) as {
      exp?: number;
      type?: string;
    };
    return (
      payload.type === "access" &&
      typeof payload.exp === "number" &&
      payload.exp * 1000 > Date.now()
    );
  } catch {
    clearSession();
    return false;
  }
}

function saveSession(accessToken: string, refreshToken: string | null, user: AuthUser): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(ACCESS_KEY, accessToken);
  if (refreshToken) localStorage.setItem(REFRESH_KEY, refreshToken);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(USER_KEY);
}

// ── API ────────────────────────────────────────────────────────────────────

export async function authLogin(email: string, password: string): Promise<AuthUser> {
  const body = new URLSearchParams({ username: email, password });
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    let errMsg = "Login failed";
    if (Array.isArray(err.detail) && err.detail.length > 0) {
      errMsg = err.detail[0].msg;
    } else if (typeof err.detail === "string") {
      errMsg = err.detail;
    }
    throw new Error(errMsg);
  }

  const data = (await res.json()) as {
    access_token: string;
    refresh_token?: string;
    token_type: string;
  };

  // Fetch profile
  const meRes = await fetch(`${API_BASE}/auth/me`, {
    headers: { Authorization: `Bearer ${data.access_token}` },
  });
  if (!meRes.ok) throw new Error("Could not load user profile");
  const user = (await meRes.json()) as AuthUser;

  saveSession(data.access_token, data.refresh_token ?? null, user);
  return user;
}

export async function authRegister(
  username: string,
  email: string,
  password: string,
): Promise<AuthUser> {
  const res = await fetch(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, email, password }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    let errMsg = "Registration failed";
    if (Array.isArray(err.detail) && err.detail.length > 0) {
      errMsg = err.detail[0].msg;
    } else if (typeof err.detail === "string") {
      errMsg = err.detail;
    }
    throw new Error(errMsg);
  }
  return (await res.json()) as AuthUser;
}

export async function authRefresh(): Promise<string | null> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return null;

  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });

  if (!res.ok) {
    clearSession();
    return null;
  }

  const data = (await res.json()) as { access_token: string };
  if (typeof window !== "undefined") {
    localStorage.setItem(ACCESS_KEY, data.access_token);
  }
  return data.access_token;
}

export function authLogout(): void {
  clearSession();
  if (typeof window !== "undefined") {
    window.location.href = "/login";
  }
}

// ── Authenticated fetch wrapper ────────────────────────────────────────────

/**
 * Drop-in replacement for `fetch()` that automatically injects the JWT
 * Authorization header and retries once on 401 using the refresh token.
 */
export async function apiFetch(input: string | URL, init?: RequestInit): Promise<Response> {
  const token = getAccessToken();
  const send = (accessToken: string | null) => {
    const headers = new Headers(init?.headers);
    if (accessToken && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${accessToken}`);
    }
    return fetch(input, { ...init, headers });
  };

  let res = await send(token);

  // On 401 try to refresh once
  if (res.status === 401 && getRefreshToken()) {
    const newToken = await authRefresh();
    if (newToken) {
      // Build a fresh request for the retry. This is required for multipart
      // uploads because a fetch request body may have been consumed already.
      res = await send(newToken);
    } else {
      // Refresh also failed — force login
      clearSession();
      if (typeof window !== "undefined") {
        window.location.href = "/login";
      }
    }
  }

  return res;
}
