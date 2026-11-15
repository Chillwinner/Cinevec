import assert from "node:assert/strict";
import test from "node:test";
import {
  authorizationHeader,
  clearToken,
  readToken,
  shouldRedirectToLogin,
  storeFromLogin,
  tokenExpired,
  writeToken,
} from "./authStorage.ts";

const memory = new Map<string, string>();

// 轻量模拟浏览器存储，不启动浏览器。
globalThis.localStorage = {
  getItem: (key: string) => memory.get(key) ?? null,
  setItem: (key: string, value: string) => {
    memory.set(key, value);
  },
  removeItem: (key: string) => {
    memory.delete(key);
  },
  clear: () => memory.clear(),
  key: (index: number) => [...memory.keys()][index] ?? null,
  get length() {
    return memory.size;
  },
} as Storage;

test("stores and clears token without logging it here", () => {
  memory.clear();
  const stored = storeFromLogin({ token: "test-token-value", username: "tester", expires_in: 60 }, 1_000);
  assert.equal(readToken()?.token, "test-token-value");
  assert.equal(stored.username, "tester");
  assert.equal(stored.expiresAt, 61_000);
  clearToken();
  assert.equal(readToken(), null);
});

test("expired token is treated as logged out", () => {
  const stored = { token: "t", expiresAt: 50 };
  assert.equal(tokenExpired(stored, 100), true);
  assert.equal(tokenExpired({ token: "t", expiresAt: 200 }, 100), false);
  assert.deepEqual(authorizationHeader(stored, 100), {});
  assert.deepEqual(authorizationHeader({ token: "abc", expiresAt: 200 }, 100), { Authorization: "Bearer abc" });
});

test("401 or missing token redirects only when auth is enabled", () => {
  assert.equal(shouldRedirectToLogin(false, 401, null), false);
  assert.equal(shouldRedirectToLogin(true, 401, { token: "t" }), true);
  assert.equal(shouldRedirectToLogin(true, 200, null), true);
  assert.equal(shouldRedirectToLogin(true, 200, { token: "t", expiresAt: Date.now() + 10_000 }), false);
  writeToken({ token: "t" });
  clearToken();
  assert.equal(readToken(), null);
});
