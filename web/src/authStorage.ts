const TOKEN_KEY = "pica.auth.token";

export type StoredToken = {
  token: string;
  username?: string;
  expiresAt?: number;
};

export function readToken(): StoredToken | null {
  if (typeof localStorage === "undefined") return null;
  const raw = localStorage.getItem(TOKEN_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as StoredToken;
    if (!parsed?.token) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function writeToken(value: StoredToken): void {
  if (typeof localStorage === "undefined") return;
  localStorage.setItem(TOKEN_KEY, JSON.stringify(value));
}

export function clearToken(): void {
  if (typeof localStorage === "undefined") return;
  localStorage.removeItem(TOKEN_KEY);
}

export function tokenExpired(value: StoredToken | null, now = Date.now()): boolean {
  if (!value?.token) return true;
  if (!value.expiresAt) return false;
  return value.expiresAt <= now;
}

export function shouldRedirectToLogin(authEnabled: boolean, status: number, stored: StoredToken | null, now = Date.now()): boolean {
  if (!authEnabled) return false;
  if (status === 401) return true;
  if (tokenExpired(stored, now)) return true;
  return !stored?.token;
}

export function authorizationHeader(stored: StoredToken | null, now = Date.now()): Record<string, string> {
  if (!stored?.token || tokenExpired(stored, now)) return {};
  return { Authorization: `Bearer ${stored.token}` };
}

export function storeFromLogin(body: { token: string; username?: string; expires_in?: number }, now = Date.now()): StoredToken {
  const expiresAt = body.expires_in ? now + body.expires_in * 1000 : undefined;
  const stored = { token: body.token, username: body.username, expiresAt };
  writeToken(stored);
  return stored;
}
