/**
 * Regression tests for the console's auth plumbing: the single-flight token
 * refresh, the session-expiry path, the download replay and the supervised
 * jobs WebSocket.
 *
 * These run the REAL `src/api/client.ts` (Node's type stripping, no build
 * step) against fake `fetch` / `WebSocket` / `localStorage` globals.
 *
 *   node --experimental-strip-types --test tests/
 *
 * They exist because of one specific production failure: the backend rotates
 * refresh tokens on every use and revokes the whole family on reuse, so when
 * the 15-minute access token expired, every in-flight request independently
 * tried to refresh with the SAME refresh token — the first rotation won and
 * the rest were treated as token theft, killing the session.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

// ---------------------------------------------------------------------------
// Fake browser globals
// ---------------------------------------------------------------------------

class FakeStorage {
  #map = new Map();
  getItem(k) { return this.#map.has(k) ? this.#map.get(k) : null; }
  setItem(k, v) { this.#map.set(String(k), String(v)); }
  removeItem(k) { this.#map.delete(k); }
  clear() { this.#map.clear(); }
}

class FakeWebSocket {
  static instances = [];
  static reset() { FakeWebSocket.instances = []; }
  constructor(url) {
    this.url = url;
    this.readyState = 0; // CONNECTING
    this.onopen = null;
    this.onclose = null;
    this.onmessage = null;
    FakeWebSocket.instances.push(this);
  }
  close(code = 1000) {
    this.readyState = 3;
    this.clientClosedWith = code;
  }
  /** Test helpers standing in for the server side. */
  serverAccept() { this.readyState = 1; this.onopen?.({ type: "open" }); }
  serverClose(code) { this.readyState = 3; this.onclose?.({ type: "close", code }); }
}

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function session(accessToken, refreshToken) {
  return {
    access_token: accessToken,
    refresh_token: refreshToken,
    token_type: "bearer",
    expires_in: 900,
    role: "INVESTIGATOR",
    badge_number: "INV-0001",
    full_name: "Inspector Sharma",
    jurisdiction_id: "RJ-JAIPUR",
    station_id: "PS-JAIPUR",
  };
}

/**
 * A fetch stub driven by an access-token rule: only tokens that the stub has
 * issued (or the initial valid one) succeed; anything else gets 401.
 * `/auth/refresh` always rotates to a new token pair — exactly once per
 * call, like the real backend.
 */
function installFetch({ okToken, refreshFailures = 0 }) {
  const log = [];
  const validTokens = new Set([okToken]);
  let refreshCalls = 0;
  let rotations = 0;
  const fetchStub = async (input, init = {}) => {
    const url = String(input instanceof Request ? input.url : input);
    const method = (init.method ?? "GET").toUpperCase();
    const authHeader =
      init.headers?.get?.("Authorization") ?? init.headers?.Authorization ?? null;
    const auth = authHeader ? authHeader.replace("Bearer ", "") : null;
    log.push({ method, url, auth, body: init.body });

    if (url.endsWith("/auth/refresh")) {
      refreshCalls += 1;
      if (refreshCalls <= refreshFailures) {
        return json({ error: { code: "authentication_failed", message: "no" } }, 401);
      }
      rotations += 1;
      const next = `${okToken}-${rotations}`;
      validTokens.add(next);
      return json(session(next, `refresh-${rotations}`));
    }
    if (auth && validTokens.has(auth)) return json({ ok: true });
    return json({ error: { code: "authentication_failed", message: "expired" } }, 401);
  };
  return { fetchStub, log, refreshCallCount: () => refreshCalls };
}

// ---------------------------------------------------------------------------
// Module loader — one fresh client module per test (client.ts keeps state)
// ---------------------------------------------------------------------------

const CLIENT_URL = new URL("../src/api/client.ts", import.meta.url).href;
let instance = 0;

async function loadClient({ okToken, refreshFailures = 0 } = {}) {
  const storage = new FakeStorage();
  const ws = FakeWebSocket;
  ws.reset();
  const timers = [];
  const delays = [];
  const setTimeoutStub = (cb, delay) => {
    timers.push({ cb, delay });
    delays.push(delay);
    return timers.length;
  };
  const clearTimeoutStub = (id) => { if (timers[id - 1]) timers[id - 1].cleared = true; };

  const realFetch = globalThis.fetch;
  const realWS = globalThis.WebSocket;
  const realStorage = globalThis.localStorage;
  const realWindow = globalThis.window;
  const realSetTimeout = globalThis.setTimeout;
  const realClearTimeout = globalThis.clearTimeout;

  const { fetchStub, log, refreshCallCount } = installFetch({ okToken, refreshFailures });
  globalThis.fetch = fetchStub;
  globalThis.WebSocket = ws;
  globalThis.localStorage = storage;
  globalThis.window = { location: { protocol: "http:", host: "console.test" } };
  globalThis.setTimeout = setTimeoutStub;
  globalThis.clearTimeout = clearTimeoutStub;

  storage.setItem("crimelink.access", okToken ? `${okToken}-0` : "");
  storage.setItem("crimelink.refresh", "refresh-0");
  storage.setItem("crimelink.user", JSON.stringify(session(`${okToken}-0`, "refresh-0")));

  instance += 1;
  const mod = await import(`${CLIENT_URL}?instance=${instance}`);

  const restore = () => {
    globalThis.fetch = realFetch;
    globalThis.WebSocket = realWS;
    globalThis.localStorage = realStorage;
    globalThis.window = realWindow;
    globalThis.setTimeout = realSetTimeout;
    globalThis.clearTimeout = realClearTimeout;
  };

  const flush = async () => {
    for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r));
  };
  /** Run pending backoff timers (oldest first). */
  const runTimers = async () => {
    while (timers.length) {
      const t = timers.shift();
      if (t.cleared || typeof t.cb !== "function") continue;
      t.cb();
      await flush();
    }
  };

  return { mod, storage, log, refreshCallCount, timers, delays, flush, runTimers, restore, WebSocket: ws };
}

// --------------------------------------------------------------------------- #
// 1. Concurrent 401s share ONE refresh; every request replays with the new token
// --------------------------------------------------------------------------- #

test("concurrent 401s trigger exactly one refresh and all replay", async () => {
  const t = await loadClient({ okToken: "tok" });
  try {
    const [a, b, c] = await Promise.all([
      t.mod.api("/cases"),
      t.mod.api("/cases/abc"),
      t.mod.api("/jobs"),
    ]);
    assert.ok(a.ok && b.ok && c.ok, "all requests succeed after refresh");
    assert.equal(t.refreshCallCount(), 1, "exactly one refresh network call");
    const refresh = t.log.find((e) => e.url.endsWith("/auth/refresh"));
    // The stale access token may ride along in the Authorization header; the
    // public refresh endpoint ignores it — rotation is driven by the body.
    assert.equal(JSON.parse(refresh.body).refresh_token, "refresh-0", "original refresh token used");
    const replays = t.log.filter((e) => !e.url.endsWith("/auth/refresh"));
    assert.ok(replays.every((e) => e.auth?.startsWith("tok-")), "replays carry the rotated token");
    assert.equal(t.storage.getItem("crimelink.access"), "tok-1", "token store updated");
  } finally { t.restore(); }
});

// --------------------------------------------------------------------------- #
// 2. Failed refresh ends the session exactly once, without a refresh storm
// --------------------------------------------------------------------------- #

test("failed refresh clears the session and notifies once", async () => {
  const t = await loadClient({ okToken: "tok", refreshFailures: 99 });
  try {
    let unauthorized = 0;
    t.mod.setUnauthorizedHandler(() => { unauthorized += 1; });
    await assert.rejects(
      Promise.all([t.mod.api("/cases"), t.mod.api("/jobs")]),
      (err) => err.code === "session_expired" && err.status === 401,
    );
    assert.equal(t.refreshCallCount(), 1, "no repeated refresh attempts");
    assert.equal(t.storage.getItem("crimelink.access"), null, "tokens cleared");
    assert.ok(unauthorized >= 1, "app is told to sign out");
  } finally { t.restore(); }
});

// --------------------------------------------------------------------------- #
// 3. A 4401 WebSocket close renews the token and reconnects with it
// --------------------------------------------------------------------------- #

test("websocket 4401 reconnects with the renewed token", async () => {
  const t = await loadClient({ okToken: "tok" });
  try {
    const stop = t.mod.jobSocket("case-1", () => {});
    assert.equal(t.WebSocket.instances.length, 1);
    assert.ok(t.WebSocket.instances[0].url.includes("token=tok-0"), "first attempt uses current token");

    t.WebSocket.instances[0].serverClose(4401);
    await t.flush();

    assert.equal(t.refreshCallCount(), 1, "one renewal after 4401");
    assert.equal(t.WebSocket.instances.length, 2, "reconnected once");
    assert.ok(t.WebSocket.instances[1].url.includes("token=tok-1"), "reconnect uses the NEW token");

    stop();
  } finally { t.restore(); }
});

test("websocket 4401 with failed renewal terminates the session, no retry storm", async () => {
  const t = await loadClient({ okToken: "tok", refreshFailures: 99 });
  try {
    let unauthorized = 0;
    t.mod.setUnauthorizedHandler(() => { unauthorized += 1; });
    const stop = t.mod.jobSocket("case-1", () => {});
    t.WebSocket.instances[0].serverClose(4401);
    await t.flush();
    assert.equal(t.WebSocket.instances.length, 1, "no further connection attempts");
    assert.equal(t.storage.getItem("crimelink.access"), null, "session cleared");
    assert.ok(unauthorized >= 1);
    stop();
  } finally { t.restore(); }
});

test("websocket 4403 (case not in scope) stops immediately without retries", async () => {
  const t = await loadClient({ okToken: "tok" });
  try {
    const stop = t.mod.jobSocket("case-forbidden", () => {});
    t.WebSocket.instances[0].serverClose(4403);
    await t.flush();
    await t.runTimers();
    assert.equal(t.WebSocket.instances.length, 1, "no reconnection attempt");
    assert.equal(t.refreshCallCount(), 0, "no renewal for an authorization denial");
    stop();
  } finally { t.restore(); }
});

test("persistent 4401 despite successful refreshes stops after a bounded number of cycles", async () => {
  const t = await loadClient({ okToken: "tok" });
  try {
    const stop = t.mod.jobSocket("case-1", () => {});
    // Server keeps rejecting even freshly rotated tokens (broken clock etc.)
    for (let i = 0; i < 6; i++) {
      t.WebSocket.instances.at(-1).serverClose(4401);
      await t.flush();
    }
    assert.ok(t.refreshCallCount() <= 2, "renewal loop is capped");
    assert.ok(t.WebSocket.instances.length <= 3, "connection loop is capped");
    stop();
  } finally { t.restore(); }
});

// --------------------------------------------------------------------------- #
// 4. Transient failures back off with a cap — no retry storm
// --------------------------------------------------------------------------- #

test("transient closes reconnect with capped exponential backoff", async () => {
  const t = await loadClient({ okToken: "tok" });
  try {
    const stop = t.mod.jobSocket("case-1", () => {});
    assert.equal(t.timers.length, 0, "no timer while connected");

    // 8 abnormal closures — far more than the cap.
    for (let i = 0; i < 8; i++) {
      t.WebSocket.instances.at(-1).serverClose(1006);
      await t.flush();
      await t.runTimers();
    }
    assert.equal(t.refreshCallCount(), 0, "no refresh attempts for non-auth failures");
    assert.equal(t.timers.length, 0, "no timer left pending after the cap");
    assert.ok(t.WebSocket.instances.length <= 1 + 5, `at most 1 initial + 5 retries, got ${t.WebSocket.instances.length}`);
    assert.deepEqual(t.delays, [1000, 2000, 4000, 8000, 15000], "backoff doubles and caps at 15s");
    stop();
  } finally { t.restore(); }
});

test("a successful open resets the backoff and auth counters", async () => {
  const t = await loadClient({ okToken: "tok" });
  try {
    const stop = t.mod.jobSocket("case-1", () => {});
    t.WebSocket.instances.at(-1).serverClose(1006);
    await t.flush();
    await t.runTimers();                      // schedules one retry after backoff
    t.WebSocket.instances.at(-1).serverAccept();  // recovered
    t.WebSocket.instances.at(-1).serverClose(1006);
    await t.flush();
    assert.deepEqual(t.delays, [1000, 1000], "backoff restarted from the floor, not stacked");
    stop();
  } finally { t.restore(); }
});

// --------------------------------------------------------------------------- #
// 5. download() replays once after a shared refresh
// --------------------------------------------------------------------------- #

test("download replays with the renewed token after a 401", async () => {
  const t = await loadClient({ okToken: "tok" });
  const realCreate = globalThis.document?.createElement;
  const realCreateObjectURL = globalThis.URL.createObjectURL;
  const realRevokeObjectURL = globalThis.URL.revokeObjectURL;
  try {
    globalThis.document = {
      createElement: () => ({ click() {}, remove() {}, set href(v) {}, set download(v) {} }),
      body: { appendChild() {}, removeChild() {} },
    };
    globalThis.URL.createObjectURL = () => "blob:test";
    globalThis.URL.revokeObjectURL = () => {};
    await t.mod.download("/cases/case-1/export", "brief.pdf");
    const calls = t.log.filter((e) => e.url.endsWith("/export"));
    assert.equal(calls.length, 2, "original + replay");
    assert.equal(calls[0].auth, "tok-0");
    assert.equal(calls[1].auth, "tok-1");
    assert.equal(t.refreshCallCount(), 1);
  } finally {
    if (realCreate) globalThis.document.createElement = realCreate; else delete globalThis.document;
    if (realCreateObjectURL) globalThis.URL.createObjectURL = realCreateObjectURL;
    if (realRevokeObjectURL) globalThis.URL.revokeObjectURL = realRevokeObjectURL;
    t.restore();
  }
});

// --------------------------------------------------------------------------- //
// cleanup without a session is a no-op (signed-out pages must not connect)
// --------------------------------------------------------------------------- //

test("jobSocket does not connect without a stored token", async () => {
  const t = await loadClient({ okToken: "tok" });
  try {
    t.storage.removeItem("crimelink.access");
    const stop = t.mod.jobSocket("case-1", () => {});
    assert.equal(t.WebSocket.instances.length, 0);
    stop();
  } finally { t.restore(); }
});
