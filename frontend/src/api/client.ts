/**
 * Thin fetch wrapper for the CrimeLink API.
 *
 * Two rules matter here:
 *  * every call goes to a *relative* /api path, so the console works unchanged
 *    behind nginx, the FastAPI static mount, or the Vite dev proxy;
 *  * a 401 triggers exactly one refresh attempt and then replays the request —
 *    a 15-minute access token must never interrupt an investigator mid-review.
 */

export type Role = "VIEWER" | "INVESTIGATOR" | "ADMIN";

export interface Session {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  role: Role;
  badge_number: string;
  full_name: string;
  jurisdiction_id: string;
  station_id: string;
}

const ACCESS_KEY = "crimelink.access";
const REFRESH_KEY = "crimelink.refresh";
const USER_KEY = "crimelink.user";

// ---------------------------------------------------------------------------
// In-flight GET deduplication
//
// Pages fetch the resources they need once per mount — and under React
// StrictMode in development an effect runs twice, so without this one page
// view can issue two identical requests.  Several pages back-to-back (Admin
// alone used to fire eleven endpoints per visit) easily burned through the
// server-side per-user limit (CRIMELINK_RATE_LIMIT_PER_MINUTE) and produced
// the 429 storm seen in the console.  Concurrent identical GETs therefore
// share a single network request; every caller still gets its own promise.
// ---------------------------------------------------------------------------
const inflight = new Map<string, Promise<unknown>>();

function dedupeKey(path: string, init: RequestInit): string | null {
  const method = (init.method ?? "GET").toUpperCase();
  return method === "GET" ? `GET ${path}` : null;
}

let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler;
}

/**
 * Drop the session and hand control to the app (which shows the login
 * screen).  Never throws, so it is safe inside fire-and-forget chains such
 * as the WebSocket supervisor.
 */
function terminateSession() {
  tokenStore.clear();
  onUnauthorized?.();
}

/**
 * End the session after a refresh that cannot be repaired.
 */
function sessionExpired(): never {
  terminateSession();
  throw new ApiError(401, "session_expired", "Your session has expired. Please sign in again.");
}

export const tokenStore = {
  get access() {
    return localStorage.getItem(ACCESS_KEY);
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY);
  },
  get user(): Session | null {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  },
  save(session: Session) {
    localStorage.setItem(ACCESS_KEY, session.access_token);
    localStorage.setItem(REFRESH_KEY, session.refresh_token);
    localStorage.setItem(USER_KEY, JSON.stringify(session));
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
    localStorage.removeItem(USER_KEY);
  },
};

/**
 * Milliseconds before the access token's expiry at which the client renews it
 * proactively — before a request has a chance to 401 and before a WebSocket
 * handshake is attempted with a token the server is about to reject.
 */
const TOKEN_REFRESH_MARGIN_MS = 60_000;

/** Decode the JWT payload of an access token without verifying it. */
function decodeJwtPayload(token: string): { exp?: number } | null {
  const parts = token.split(".");
  if (parts.length < 2) return null;
  try {
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, "=");
    const decoded = atob(padded);
    return JSON.parse(decoded) as { exp?: number };
  } catch {
    return null;
  }
}

/**
 * Renew the access token if it is within the refresh margin of expiring (or
 * already expired).  Runs before outbound requests and WebSocket connects so
 * the expiry+replay dance — and the corresponding 401 / 4401 console noise —
 * happens as rarely as possible.  Reuses the single shared refresh promise.
 */
export async function ensureFreshToken(): Promise<void> {
  const token = tokenStore.access;
  if (!token || !tokenStore.refresh) return;
  const payload = decodeJwtPayload(token);
  if (!payload || typeof payload.exp !== "number") return;
  if (payload.exp * 1000 - Date.now() < TOKEN_REFRESH_MARGIN_MS) {
    await refreshSession().catch(() => undefined);
  }
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields?: { field: string; message: string }[];

  constructor(status: number, code: string, message: string, fields?: { field: string; message: string }[]) {
    super(message);
    this.status = status;
    this.code = code;
    this.fields = fields;
  }
}

async function parse(response: Response): Promise<unknown> {
  if (response.status === 204) return null;
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function messageFrom(payload: unknown, status: number): { message: string; code: string; fields?: { field: string; message: string }[] } {
  if (payload && typeof payload === "object" && "error" in payload) {
    const error = (payload as { error: { code?: string; message?: string; fields?: { field: string; message: string }[] } }).error;
    return {
      code: error.code ?? String(status),
      message: error.message ?? "Request failed.",
      fields: error.fields,
    };
  }
  return { code: String(status), message: `Request failed (${status}).` };
}

let activeDatasetIdCached: string | null = null;

export function setActiveDatasetId(id: string | null): void {
  activeDatasetIdCached = id;
}

export function getActiveDatasetId(): string | null {
  return activeDatasetIdCached;
}

async function raw(path: string, init: RequestInit = {}, token?: string | null): Promise<Response> {
  const headers = new Headers(init.headers);
  const bearer = token ?? tokenStore.access;
  if (bearer) headers.set("Authorization", `Bearer ${bearer}`);
  if (activeDatasetIdCached && !headers.has("X-Dataset-Id")) {
    headers.set("X-Dataset-Id", activeDatasetIdCached);
  }
  if (!(init.body instanceof FormData) && init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return fetch(`/api/v1${path}`, { ...init, headers });
}

async function requestRefresh(): Promise<boolean> {
  const refresh = tokenStore.refresh;
  if (!refresh) return false;
  try {
    const response = await raw("/auth/refresh", {
      method: "POST",
      body: JSON.stringify({ refresh_token: refresh }),
    }, null);
    if (!response.ok) {
      if (response.status === 401 || response.status === 404) {
        tokenStore.clear();
        onUnauthorized?.();
      }
      return false;
    }
    const session = (await parse(response)) as Session;
    tokenStore.save(session);
    return true;
  } catch {
    return false;
  }
}

/**
 * Renew the access token — at most ONE network refresh at a time.
 *
 * The backend rotates refresh tokens on every use and treats a *reused*
 * refresh token as theft: the whole token family is revoked.  When the
 * 15-minute access token expires, a dozen in-flight requests can 401 within
 * the same second; if each of them refreshed independently, the first
 * rotation would invalidate the token all the others are still holding and
 * the reuse detector would kill the session.  Sharing one in-flight refresh
 * turns that race into a single rotation that every waiter replays against.
 */
let refreshInFlight: Promise<boolean> | null = null;

export function refreshSession(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = requestRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

async function apiInner<T>(path: string, init: RequestInit = {}): Promise<T> {
  // Renew the access token *before* it expires so a request never has to 401
  // and replay in the first place (the replay path remains as a safety net).
  await ensureFreshToken();
  let response = await raw(path, init);
  if (response.status === 401 && tokenStore.refresh) {
    const renewed = await refreshSession();
    if (renewed) {
      response = await raw(path, init);
    } else {
      sessionExpired();
    }
  }
  const payload = await parse(response);
  if (!response.ok) {
    const { code, message, fields } = messageFrom(payload, response.status);
    throw new ApiError(response.status, code, message, fields);
  }
  return payload as T;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const key = dedupeKey(path, init);
  if (key) {
    const existing = inflight.get(key);
    if (existing) return existing as Promise<T>;
  }
  const request = apiInner<T>(path, init);
  if (key) {
    inflight.set(key, request);
    // Clear the slot when the request settles.  The extra .catch keeps the
    // cleanup promise from surfacing as an unhandled rejection — callers of
    // `request` still receive the original error.
    request
      .finally(() => inflight.delete(key))
      .catch(() => undefined);
  }
  return request;
}

/**
 * Forget every in-flight GET dedup slot.
 *
 * Called when the active dataset is replaced: a response that is already on
 * the wire was produced from the OLD dataset's rows, and replaying it to the
 * next mount would put yesterday's corpus back on screen. Dropping the slots
 * means the next fetch is a real fetch.
 */
export function clearInflight(): void {
  inflight.clear();
}

/**
 * Signal to the whole application that the active dataset has changed.
 *
 * This does two things atomically:
 *  1. Drops all in-flight GET dedup slots so no stale response can be
 *     replayed to the next page mount.
 *  2. Dispatches a ``crimelink:dataset-changed`` CustomEvent on ``window``
 *     so any page or component that has cached API results (case lists,
 *     entity tables, etc.) can listen and immediately clear its local state
 *     rather than showing the previous dataset's data until the next
 *     navigation.
 *
 * Called by DatasetConsole after a successful import or explicit activation.
 */
export function datasetChanged(newDatasetId?: string | null): void {
  if (newDatasetId !== undefined) {
    activeDatasetIdCached = newDatasetId;
  }
  clearInflight();
  try {
    window.dispatchEvent(new CustomEvent("crimelink:dataset-changed"));
  } catch {
    /* CustomEvent is not available in SSR / test environments — ignore */
  }
}

/**
 * Fetch a binary body (a source file's raw bytes) with the session token.
 *
 * PDFs and images are rendered by the browser from their actual bytes — the
 * pipeline never pipes binary through a text response — so the viewer asks
 * for a Blob and turns it into an object URL. The same 401-refresh-then-
 * replay contract as `download()` applies.
 */
export async function fetchBlob(path: string): Promise<Blob> {
  let response = await fetch(`/api/v1${path}`, { headers: authHeaders() });
  if (response.status === 401 && tokenStore.refresh) {
    if (await refreshSession()) {
      response = await fetch(`/api/v1${path}`, { headers: authHeaders() });
    } else {
      sessionExpired();
    }
  }
  if (!response.ok) {
    const detail = await parse(response);
    const { message } = messageFrom(detail, response.status);
    throw new ApiError(response.status, String(response.status), message);
  }
  return response.blob();
}

/**
 * Fetch a binary artefact (the watermarked PDF brief) with the session token and
 * hand it to the browser as a download.
 *
 * A plain <a download> cannot carry the Authorization header, so the file is
 * pulled with the session attached and turned into an object URL.
 */
export async function download(path: string, filename: string): Promise<void> {
  let response = await fetch(`/api/v1${path}`, { headers: authHeaders() });
  if (response.status === 401 && tokenStore.refresh) {
    // Same contract as api(): one shared refresh attempt, then replay.
    if (await refreshSession()) {
      response = await fetch(`/api/v1${path}`, { headers: authHeaders() });
    } else {
      sessionExpired();
    }
  }
  if (!response.ok) {
    const detail = await parse(response);
    const { message } = messageFrom(detail, response.status);
    throw new ApiError(response.status, String(response.status), message);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function authHeaders(): HeadersInit {
  const token = tokenStore.access;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function setupStatus(): Promise<{ setup_required: boolean }> {
  const response = await raw("/auth/setup", { method: "GET" }, null);
  const payload = await parse(response);
  if (!response.ok) {
    const { code, message, fields } = messageFrom(payload, response.status);
    throw new ApiError(response.status, code, message, fields);
  }
  return payload as { setup_required: boolean };
}

export interface SetupPayload {
  badge_number: string;
  full_name: string;
  password: string;
  station_id: string;
  jurisdiction_id: string;
}

export async function completeSetup(body: SetupPayload): Promise<Session> {
  const response = await raw("/auth/setup", {
    method: "POST",
    body: JSON.stringify(body),
  }, null);
  const payload = await parse(response);
  if (!response.ok) {
    const { code, message, fields } = messageFrom(payload, response.status);
    throw new ApiError(response.status, code, message, fields);
  }
  const session = payload as Session;
  tokenStore.save(session);
  return session;
}

export async function login(badgeNumber: string, password: string): Promise<Session> {
  const response = await raw("/auth/login", {
    method: "POST",
    body: JSON.stringify({ badge_number: badgeNumber, password }),
  }, null);
  const payload = await parse(response);
  if (!response.ok) {
    const { code, message, fields } = messageFrom(payload, response.status);
    throw new ApiError(response.status, code, message, fields);
  }
  const session = payload as Session;
  tokenStore.save(session);
  return session;
}

export async function logout(): Promise<void> {
  const refresh = tokenStore.refresh;
  if (refresh) {
    try {
      await api("/auth/logout", { method: "POST", body: JSON.stringify({ refresh_token: refresh }) });
    } catch {
      /* signing out must never be blocked by a network error */
    }
  }
  tokenStore.clear();
}

export async function uploadDocument(
  caseId: string,
  file: File,
  documentType: string,
  sourceConfidence: string,
): Promise<{ document_id: string; job_id: string }> {
  const body = new FormData();
  body.append("file", file);
  body.append("document_type", documentType);
  body.append("source_confidence", sourceConfidence);
  return api(`/cases/${caseId}/documents`, { method: "POST", body });
}

/**
 * Live processing status for a case (PRD: "Stage 3/6 — NLP extraction").
 *
 * The channel authenticates with the access token at connect time.  When the
 * server closes the socket with code 4401 the token was rejected: renew it
 * (one shared refresh) and reconnect with the NEW token.  Any other abnormal
 * close — backend restart, network blip, a proxy that drops upgrade requests
 * — is retried with a bounded backoff, and if the channel still will not stay
 * up the supervisor degrades honestly to polling and tells the UI via
 * ``onStatus``.  It never fabricates "connected": the page shows either the
 * live channel or an explicit polling fallback.
 */
const WS_MAX_RETRY_ATTEMPTS = 5;
const WS_MAX_RETRY_DELAY_MS = 15_000;
// Renewals that keep failing to produce an accepted socket before the
// supervisor gives up instead of looping refresh -> connect forever.
const WS_MAX_AUTH_RETRIES = 2;
// Coarse fallback cadence when the WebSocket cannot be established at all.
const WS_POLL_INTERVAL_MS = 5000;

export type JobSocketStatus =
  | { state: "connected" }
  | { state: "polling"; reason: string };

export function jobSocket(
  caseId: string,
  onMessage: (event: unknown) => void,
  onStatus?: (status: JobSocketStatus) => void,
): () => void {
  let stopped = false;
  let socket: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let attempts = 0; // consecutive non-auth failures since the last open
  let authRetries = 0; // 4401 cycles since the last successful open

  const stopPolling = () => {
    if (pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  const startPolling = (reason: string) => {
    if (stopped || pollTimer !== null) return;
    onStatus?.({ state: "polling", reason });
    pollTimer = setInterval(() => {
      if (stopped) {
        stopPolling();
        return;
      }
      onMessage({ type: "poll_tick" });
    }, WS_POLL_INTERVAL_MS);
  };

  const connect = () => {
    if (stopped) return;
    const token = tokenStore.access;
    if (!token) return; // signed out — nothing to subscribe with
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(
      `${scheme}://${window.location.host}/api/v1/jobs/ws/${caseId}?token=${encodeURIComponent(token)}`,
    );
    socket.onopen = () => {
      attempts = 0;
      authRetries = 0;
      stopPolling();
      onStatus?.({ state: "connected" });
    };
    socket.onmessage = (event) => {
      try {
        onMessage(JSON.parse(event.data));
      } catch {
        /* ignore malformed frames */
      }
    };
    socket.onclose = (event) => {
      socket = null;
      if (stopped) return;
      if (event.code === 4403) {
        // Authenticated, but this session may not watch the case.  That
        // verdict will not change on retry — stop immediately.
        stopPolling();
        return;
      }
      if (event.code === 4401) {
        // The access token expired or was rejected.  Renew it once and
        // reconnect with the fresh token; if renewal is refused the session
        // is over, so terminate cleanly instead of retrying into a wall.
        if (authRetries >= WS_MAX_AUTH_RETRIES) return;
        authRetries += 1;
        void refreshSession().then((renewed) => {
          if (stopped) return;
          if (renewed) {
            connect();
          } else {
            terminateSession();
          }
        });
        return;
      }
      if (attempts >= WS_MAX_RETRY_ATTEMPTS) {
        // The channel cannot be held open (e.g. the connection never got
        // established — a proxy or the server dropped the upgrade).  Be
        // honest: fall back to polling rather than reporting a live feed.
        startPolling(`websocket_closed_code_${event.code || 1006}`);
        return;
      }
      const delay = Math.min(1000 * 2 ** attempts, WS_MAX_RETRY_DELAY_MS);
      attempts += 1;
      timer = setTimeout(connect, delay);
    };
  };

  connect();

  return () => {
    stopped = true;
    if (timer !== null) clearTimeout(timer);
    stopPolling();
    if (socket) {
      socket.onclose = null;
      socket.onmessage = null;
      socket.onerror = null;
      if (socket.readyState === WebSocket.CONNECTING) {
        const s = socket;
        s.onopen = () => {
          try {
            s.close(1000, "unmounted");
          } catch {
            /* ignore */
          }
        };
      } else {
        try {
          socket.close(1000, "unmounted");
        } catch {
          /* ignore */
        }
      }
      socket = null;
    }
  };
}

// ---------------------------------------------------------------------------
// Dataset / graph-build jobs
//
// Long-running work returns a job id immediately; the UI then watches that job
// over a WebSocket and, if the socket cannot be held open, over polling.  The
// two carry the *same* payload — `GET /datasets/jobs/{id}` returns what the
// socket pushes — so falling back changes the latency and nothing else.
//
// The rule this section exists to enforce: an investigator must never be
// stranded on a permanent "building…" because a proxy ate a WebSocket upgrade.
// ---------------------------------------------------------------------------

export interface DatasetJob {
  id: string;
  dataset_id: string | null;
  kind: string;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";
  stage: string | null;
  progress_pct: number;
  message: string | null;
  steps: { stage: string; message?: string | null; at: string }[];
  result: Record<string, unknown>;
  error: string | null;
  created_at: string | null;
  updated_at: string | null;
  finished_at: string | null;
  terminal: boolean;
}

export interface DatasetSummary {
  id: string;
  name: string;
  version: string;
  status: string;
  is_active: boolean;
  source_kind: string;
  stage: string;
  error: string | null;
  stats: Record<string, unknown>;
  graph_built_at: string | null;
  created_at: string | null;
  activated_at: string | null;
}

export function listDatasets(): Promise<{ items: DatasetSummary[] }> {
  return api("/datasets");
}

export function activeDataset(): Promise<{ active: DatasetSummary | null }> {
  return api("/datasets/active");
}

/**
 * Make a dataset the active one.
 *
 * The server also starts a graph rebuild so the graph follows the tables, and
 * returns its `job_id`; the console watches it like any other job.
 */
export function activateDataset(
  datasetId: string,
): Promise<DatasetSummary & { job_id: string | null; job?: DatasetJob }> {
  return api(`/datasets/${datasetId}/activate`, { method: "POST" });
}

/**
 * Upload and import a dataset.
 *
 * Takes whatever the file picker produced: one file, many files, a ZIP, or a
 * whole folder. When the browser supplies `webkitRelativePath` (a folder
 * pick), the layout is sent alongside the files so provenance survives the
 * upload — the server treats those paths as untrusted and re-derives safe
 * ones, but the structure is real information and worth preserving.
 *
 * Resolves with a `job_id` as soon as the upload lands; the import itself is
 * watched with `watchDatasetJob`.
 */
export function importDatasetFiles(
  files: File[],
  options: { name?: string; version?: string; activate?: boolean; buildGraph?: boolean } = {},
): Promise<{ job_id: string; job: DatasetJob; files_received: number }> {
  const form = new FormData();
  for (const file of files) {
    form.append("files", file, file.name);
    // Parallel array: index i of `paths` describes index i of `files`.
    const relative = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
    form.append("paths", relative || file.name);
  }
  if (options.name) form.append("name", options.name);
  if (options.version) form.append("version", options.version);
  form.append("activate", String(options.activate ?? true));
  form.append("build_graph", String(options.buildGraph ?? true));
  return api("/datasets/import", { method: "POST", body: form });
}

/** Import a dataset that already sits on the server's disk (e.g. a mounted corpus). */
export function importDatasetFromPath(
  path: string,
  options: { name?: string; version?: string; activate?: boolean } = {},
): Promise<{ job_id: string; job: DatasetJob; source: string }> {
  return api("/datasets/import/path", {
    method: "POST",
    body: JSON.stringify({
      path,
      name: options.name,
      version: options.version,
      activate: options.activate ?? true,
    }),
  });
}

/** Start a graph rebuild. Resolves as soon as the job exists, not when it ends. */
export function rebuildDatasetGraph(
  datasetId: string,
): Promise<{ job_id: string; job: DatasetJob }> {
  return api(`/datasets/${datasetId}/graph/rebuild`, { method: "POST" });
}

/** The authoritative job state. Polling and the socket both resolve to this. */
export function getDatasetJob(jobId: string): Promise<DatasetJob> {
  return api(`/datasets/jobs/${jobId}`);
}

/**
 * How one tabular file was mapped onto the canonical schema.
 *
 * Column mapping is inference. When the values under a header contradict it —
 * a `person_id` column full of dates, an `amount` column full of person keys —
 * the mapper believes the values and says so here, so an operator can accept
 * the result or fix the source rather than meeting the damage later as a
 * nonsense row on the People page.
 */
export interface DatasetMapping {
  file_id: string;
  path: string;
  filename: string;
  semantic_type: string;
  confidence: number;
  row_count: number;
  sheets: string[];
  unmapped_columns: string[];
  columns: Record<string, { column: string; canonical: string | null; confidence: number; basis: string }[]>;
  notes: {
    sheet: string;
    semantic_type: string;
    confidence: number;
    needs_review: boolean;
    contradictions: string[];
    notes: string[];
  }[];
  needs_review: boolean;
  accepted_at: string | null;
  accepted_by: string | null;
}

export function listDatasetMappings(
  datasetId: string,
  options: { needsReview?: boolean } = {},
): Promise<{ dataset_id: string; items: DatasetMapping[]; total: number; review_pending: number }> {
  const query = options.needsReview ? "?needs_review=true" : "";
  return api(`/datasets/${datasetId}/mappings${query}`);
}

/** Record operator sign-off on inferred mappings. Empty list means "all of them". */
export function acceptDatasetMappings(
  datasetId: string,
  fileIds: string[] = [],
): Promise<{ dataset_id: string; accepted: number }> {
  return api(`/datasets/${datasetId}/mappings/accept`, {
    method: "POST",
    body: JSON.stringify({ file_ids: fileIds }),
  });
}

/** How the UI is currently receiving progress, reported honestly. */
export type JobWatchTransport =
  | { transport: "websocket" }
  | { transport: "polling"; reason: string };

/** Poll cadence when the socket is unavailable. Fast enough to feel live. */
const JOB_POLL_INTERVAL_MS = 1500;
/** Socket attempts before giving up on it and polling instead. */
const JOB_WS_MAX_ATTEMPTS = 2;

/**
 * Watch one dataset/graph job to completion.
 *
 * Tries the WebSocket first. If the handshake fails, the socket closes
 * abnormally, or the token is rejected beyond renewal, it switches to polling
 * the same job — and says so through `onTransport` rather than pretending the
 * feed is live.
 *
 * Guarantees:
 *  - exactly one transport is active at a time (no duplicate subscriptions);
 *  - the returned function cancels everything and is safe to call twice;
 *  - `onUpdate` always receives the terminal state before the watch ends,
 *    whichever transport delivered it;
 *  - nothing here can cancel or fail the job itself — it is a read-only view.
 */
export function watchDatasetJob(
  jobId: string,
  onUpdate: (job: DatasetJob) => void,
  onTransport?: (status: JobWatchTransport) => void,
): () => void {
  let stopped = false;
  let socket: WebSocket | null = null;
  let pollTimer: ReturnType<typeof setTimeout> | null = null;
  let attempts = 0;
  let authRetries = 0;
  let finished = false;

  const finish = (job: DatasetJob) => {
    onUpdate(job);
    if (job.terminal) {
      finished = true;
      cleanup();
    }
  };

  const cleanup = () => {
    if (pollTimer !== null) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
    if (socket) {
      socket.onclose = null;
      socket.onmessage = null;
      socket.onerror = null;
      if (socket.readyState === WebSocket.CONNECTING) {
        const s = socket;
        s.onopen = () => {
          try {
            s.close(1000, "unmounted");
          } catch {
            /* ignore */
          }
        };
      } else {
        try {
          socket.close(1000, "unmounted");
        } catch {
          /* already closing */
        }
      }
      socket = null;
    }
  };

  const poll = (reason: string) => {
    if (stopped || finished || pollTimer !== null) return;
    onTransport?.({ transport: "polling", reason });
    const tick = () => {
      if (stopped || finished) return;
      void getDatasetJob(jobId)
        .then((job) => {
          if (stopped || finished) return;
          finish(job);
          if (!job.terminal) pollTimer = setTimeout(tick, JOB_POLL_INTERVAL_MS);
        })
        .catch(() => {
          // A transient API error must not end the watch: the job is still
          // running on the server and the next tick may well succeed.
          if (!stopped && !finished) pollTimer = setTimeout(tick, JOB_POLL_INTERVAL_MS);
        });
    };
    tick();
  };

  const connect = () => {
    if (stopped || finished) return;
    const token = tokenStore.access;
    if (!token) {
      poll("not_signed_in");
      return;
    }
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    let candidate: WebSocket;
    try {
      candidate = new WebSocket(
        `${scheme}://${window.location.host}/api/v1/jobs/ws/job/${encodeURIComponent(
          jobId,
        )}?token=${encodeURIComponent(token)}`,
      );
    } catch {
      poll("websocket_unavailable");
      return;
    }
    socket = candidate;

    candidate.onopen = () => {
      attempts = 0;
      if (pollTimer !== null) {
        clearTimeout(pollTimer);
        pollTimer = null;
      }
      onTransport?.({ transport: "websocket" });
    };
    candidate.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as DatasetJob & { type?: string };
        if (payload && typeof payload.status === "string") finish(payload);
      } catch {
        /* ignore malformed frames */
      }
    };
    candidate.onclose = (event) => {
      socket = null;
      if (stopped || finished) return;
      if (event.code === 1000) {
        // Clean close: the server signalled the job ended. Confirm once via
        // the API so the UI lands on the authoritative terminal state even if
        // the final frame was lost in transit.
        void getDatasetJob(jobId).then(finish).catch(() => poll("socket_closed"));
        return;
      }
      if (event.code === 4401 && authRetries < 2) {
        authRetries += 1;
        void refreshSession().then((renewed) => {
          if (stopped || finished) return;
          if (renewed) connect();
          else poll("session_expired");
        });
        return;
      }
      attempts += 1;
      if (attempts >= JOB_WS_MAX_ATTEMPTS) {
        poll(`websocket_closed_code_${event.code || 1006}`);
        return;
      }
      setTimeout(connect, 500 * attempts);
    };
  };

  // Fetch the current state straight away so the UI is never blank while the
  // handshake is in flight, then attach the live transport.
  void getDatasetJob(jobId)
    .then((job) => {
      if (stopped) return;
      finish(job);
      if (!job.terminal) connect();
    })
    .catch(() => {
      if (!stopped) connect();
    });

  return () => {
    stopped = true;
    cleanup();
  };
}

// ---------------------------------------------------------------------------
// Investigation workflow (PRD 21: explicit, gated stages — never page-load
// side effects).  Every function maps to one investigation endpoint.
// ---------------------------------------------------------------------------

export type StageStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";

export interface InvestigationStage {
  stage: number;
  key: string;
  label: string;
  requires: number[];
  status: StageStatus;
  detail: Record<string, unknown>;
  error: string | null;
  attempt_count: number;
  finished_at: string | null;
  duration_ms: number | null;
  runnable: boolean;
  blocked_by: number[];
}

export interface InvestigationState {
  case_id: string;
  stages: InvestigationStage[];
  documents: { total: number; processed: number; pending: number; failed?: number };
  graph_backend: string;
}

export function investigationState(caseId: string): Promise<InvestigationState> {
  return api<InvestigationState>(`/cases/${caseId}/investigation`);
}

export function runInvestigationStage(
  caseId: string,
  stageKey: string,
): Promise<{ stage: number; key: string; status: string; detail: Record<string, unknown>; duration_ms: number }> {
  return api(`/cases/${caseId}/investigation/${stageKey}/run`, { method: "POST" });
}

export interface PersonTarget {
  provenance_key: string;
  name: string;
  aliases: string[];
  connections: number;
  source_doc_ids: string[];
}

export function casePersons(
  caseId: string,
): Promise<{ case_id: string; total_persons: number; items: PersonTarget[] }> {
  return api(`/cases/${caseId}/persons`);
}

export interface NodeEvidence {
  source_doc_id?: string | null;
  text_span?: number[] | null;
  origin?: {
    file: string;
    row?: number | null;
    record_id?: string | null;
    fields?: string[];
    values?: Record<string, string>;
  } | null;
}

export interface GraphNodeRow {
  provenance_key: string;
  label: string;
  name: string;
  confidence: number;
  case_ids: string[];
  source_doc_ids: string[];
  aliases: string[];
  staging: boolean;
  is_active: boolean;
  evidence: NodeEvidence | null;
  properties: Record<string, unknown>;
}

export interface GraphEdgeRow {
  key: string;
  source: string;
  target: string;
  rel_type: string;
  confidence: number;
  source_doc_ids: string[];
  source_doc_id?: string | null;
  staging: boolean;
  evidence: NodeEvidence | null;
  properties: Record<string, unknown>;
}

export interface PersonNetwork {
  case_id: string;
  /** The depth that was requested (echoed back). */
  depth: number;
  requested_depth: number;
  /** The deepest layer that actually produced nodes. */
  max_depth_reached: number;
  /** True when traversal ran out of graph before it ran out of hops. */
  exhausted: boolean;
  target: GraphNodeRow;
  truncated: boolean;
  node_limit: number | null;
  /** Layer index -> node count. Sparse: only layers that exist appear. */
  layers: Record<string, number>;
  counts: {
    nodes: number;
    edges: number;
    by_label: Record<string, number>;
    by_rel_type: Record<string, number>;
  };
  nodes: GraphNodeRow[];
  edges: GraphEdgeRow[];
}

/** Default hop depth — a starting point, not a ceiling. */
export const DEFAULT_NETWORK_DEPTH = 3;

/**
 * Fetch the person-centric network out to `depth` hops.
 *
 * `depth` is an arbitrary positive integer: 3, 13, 26 and 100 are all valid.
 * The backend walks as far as the graph actually reaches and reports
 * `exhausted` when the whole connected component has been returned, so a
 * request for more hops than exist is answered honestly rather than refused.
 */
export function personNetwork(
  caseId: string,
  personKey: string,
  depth: number = DEFAULT_NETWORK_DEPTH,
  limit?: number,
): Promise<PersonNetwork> {
  const hops = Math.max(1, Math.floor(Number(depth) || DEFAULT_NETWORK_DEPTH));
  const params = new URLSearchParams({ depth: String(hops) });
  if (limit && limit > 0) params.set("limit", String(Math.floor(limit)));
  return api(
    `/cases/${caseId}/network/${encodeURIComponent(personKey)}?${params.toString()}`,
  );
}

/**
 * The **Master Graph**: the complete, evidence-backed network of the case.
 * ``labels`` / ``relTypes`` restrict the view (the same canonical data, just
 * filtered) — they never change what is persisted.
 */
export interface CaseGraph {
  case_id: string;
  include_staging: boolean;
  truncated: boolean;
  filters: { labels: string[]; rel_types: string[] };
  counts: {
    nodes: number;
    edges: number;
    by_label: Record<string, number>;
    by_rel_type: Record<string, number>;
  };
  nodes: GraphNodeRow[];
  edges: GraphEdgeRow[];
}

export function caseGraph(
  caseId: string,
  opts: { includeStaging?: boolean; labels?: string[]; relTypes?: string[] } = {},
): Promise<CaseGraph> {
  const params = new URLSearchParams();
  if (opts.includeStaging) params.set("include_staging", "true");
  if (opts.labels?.length) params.set("labels", opts.labels.join(","));
  if (opts.relTypes?.length) params.set("rel_types", opts.relTypes.join(","));
  const qs = params.toString();
  return api(`/graph/cases/${caseId}${qs ? `?${qs}` : ""}`);
}

/** A dated event inside a temporal window — drives the timeline strip. */
export interface TemporalEvent {
  provenance_key: string;
  event_type: string | null;
  name: string;
  timestamp: string | null;
  description: string | null;
}

/**
 * The **Temporal Graph**: a time-constrained visual subgraph (NOT a serialised
 * path).  Carries the window that produced it plus the dated events inside it.
 */
export interface TemporalGraph {
  case_id: string;
  target: string | null;
  depth: number;
  empty_reason: string | null;
  time_range: { from: string | null; to: string | null; first: string | null; last: string | null };
  events: TemporalEvent[];
  counts: {
    nodes: number;
    edges: number;
    by_label: Record<string, number>;
    by_rel_type: Record<string, number>;
  };
  nodes: GraphNodeRow[];
  edges: GraphEdgeRow[];
}

export function temporalGraph(
  caseId: string,
  opts: { target?: string; fromTs?: string; toTs?: string; depth?: number } = {},
): Promise<TemporalGraph> {
  const params = new URLSearchParams();
  if (opts.target) params.set("target", opts.target);
  if (opts.fromTs) params.set("from_ts", opts.fromTs);
  if (opts.toTs) params.set("to_ts", opts.toTs);
  if (opts.depth) params.set("depth", String(opts.depth));
  const qs = params.toString();
  return api(`/graph/cases/${caseId}/temporal${qs ? `?${qs}` : ""}`);
}

export interface Finding {
  id: string;
  finding_type: string;
  title: string;
  narrative: string;
  reason: string;
  confidence: number;
  confidence_band: "HIGH" | "MEDIUM" | "LOW";
  method: string;
  entity_keys: string[];
  evidence: Record<string, unknown>[];
  details: Record<string, unknown>;
  status: "NEW" | "CONFIRMED" | "DISMISSED";
  review_note: string | null;
  created_at: string;
}

export function caseFindings(caseId: string): Promise<{ items: Finding[] }> {
  return api(`/cases/${caseId}/findings`);
}

export function personFindings(
  caseId: string,
  personKey: string,
): Promise<{ items: Finding[]; target: string }> {
  return api(`/cases/${caseId}/network/${encodeURIComponent(personKey)}/findings`);
}

export function reviewFinding(
  caseId: string,
  findingId: string,
  decision: "CONFIRMED" | "DISMISSED",
  note?: string,
): Promise<{ id: string; status: string }> {
  return api(`/cases/${caseId}/findings/${findingId}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, note: note ?? null }),
  });
}

// ---------------------------------------------------------------------------
// Active-dataset staleness guard
//
// Replacing the dataset is an admin action that happens in the Administration
// tab; every other tab holds React state (selected case, graph canvas, search
// results) that belongs to the PREVIOUS dataset. The backend already refuses
// those ids — a bookmarked CASE_0001 from dataset A 404s once dataset B is
// active — but an investigator should not have to discover that by clicking
// through dead pages. So the console watches which dataset is active and, the
// moment it changes, drops cached responses and remounts the current view.
// ---------------------------------------------------------------------------

const DATASET_POLL_MS = 12_000;

/**
 * Call `onChange` whenever the ACTIVE dataset differs from the one seen at
 * subscribe time (import completed elsewhere, activation flipped in another
 * tab). Polling — plus a re-check when the window regains focus — works
 * through any proxy and needs no server push; the server's visibility rules
 * stay authoritative either way.
 */
export function watchActiveDataset(onChange: (datasetId: string | null) => void): () => void {
  let stopped = false;
  let baseline: string | null | undefined; // undefined until first check lands

  const check = async () => {
    try {
      const { active } = await activeDataset();
      const id = active?.id ?? null;
      activeDatasetIdCached = id;
      if (baseline === undefined) {
        baseline = id;
        return;
      }
      if (id !== baseline) {
        baseline = id;
        if (!stopped) onChange(id);
      }
    } catch {
      /* a transient failure just means we check again on the next beat */
    }
  };

  void check();
  const timer = setInterval(() => void check(), DATASET_POLL_MS);
  const onFocus = () => void check();
  window.addEventListener("focus", onFocus);
  return () => {
    stopped = true;
    clearInterval(timer);
    window.removeEventListener("focus", onFocus);
  };
}

// ---------------------------------------------------------------------------
// Streaming AI answers
//
// `POST /ai/cases/{id}/ask/stream` emits NDJSON events (ack → stage →
// retrieval → delta* → done/error). The stream is the fast, progressive
// path; the plain `api()` POST remains the fallback whenever streaming is
// unavailable (proxy buffering, older server, fetch-stream errors) — the UI
// must never appear frozen just because a transport is missing, which is the
// same rule the job WebSocket follows with its polling fallback.
// ---------------------------------------------------------------------------

export interface AiStreamEvent {
  type: string;
  [key: string]: unknown;
}

export interface AskStreamHandlers {
  onAck?: (event: AiStreamEvent) => void;
  onStage?: (event: AiStreamEvent) => void;
  onDelta?: (text: string) => void;
  onDone?: (response: Record<string, unknown>) => void;
  onError?: (event: AiStreamEvent) => void;
  /** Streaming died before producing anything usable; caller may fall back. */
  onFallback?: () => void;
}

/**
 * Ask one question with progressive rendering. Resolves when the stream ends
 * (normally or not); `onFallback` fires at most once if no usable event ever
 * arrived, so the caller's non-streaming path takes over seamlessly.
 */
export async function askCaseStream(
  caseId: string,
  question: string,
  handlers: AskStreamHandlers,
  extra: { depth?: number; targetKey?: string | null } = {},
): Promise<void> {
  const body: Record<string, unknown> = { question };
  if (extra.depth) body.depth = extra.depth;
  if (extra.targetKey) body.target_key = extra.targetKey;

  let received = false;
  const mark = () => {
    received = true;
  };
  const dispatch = (event: AiStreamEvent) => {
    mark();
    switch (event.type) {
      case "ack":
        handlers.onAck?.(event);
        break;
      case "stage":
      case "retrieval":
        handlers.onStage?.(event);
        break;
      case "delta":
        handlers.onDelta?.(String(event.text ?? ""));
        break;
      case "done":
        handlers.onDone?.((event.response ?? {}) as Record<string, unknown>);
        break;
      case "error":
        handlers.onError?.(event);
        break;
      default:
        break;
    }
  };

  try {
    let response = await fetch(`/api/v1/ai/cases/${encodeURIComponent(caseId)}/ask/stream`, {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (response.status === 401 && tokenStore.refresh) {
      if (await refreshSession()) {
        response = await fetch(`/api/v1/ai/cases/${encodeURIComponent(caseId)}/ask/stream`, {
          method: "POST",
          headers: { ...authHeaders(), "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      }
    }
    if (!response.ok || !response.body) {
      if (!received) handlers.onFallback?.();
      else if (!response.ok) {
        handlers.onError?.({ type: "error", message: `Streaming failed (${response.status}).` });
      }
      return;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline: number;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (!line) continue;
        try {
          dispatch(JSON.parse(line) as AiStreamEvent);
        } catch {
          /* ignore malformed frames; the final `done` event is authoritative */
        }
      }
    }
    if (buffer.trim()) {
      try {
        dispatch(JSON.parse(buffer) as AiStreamEvent);
      } catch {
        /* truncated tail frame: the plain POST fallback covers completeness */
      }
    }
    if (!received) handlers.onFallback?.();
  } catch {
    if (!received) handlers.onFallback?.();
    else handlers.onError?.({ type: "error", message: "The answer stream was interrupted." });
  }
}
