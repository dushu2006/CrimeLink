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
  /** Source-derived legal/criminal status; never inferred from centrality. */
  criminal_status?: string | null;
  /** True only when a source document records a criminal/legal status. */
  is_criminal?: boolean;
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
  view?: "PERSON NETWORK" | "PERSON + CONTEXT" | "EVIDENCE VIEW" | "FINDING SUBGRAPH";
  available_views?: string[];
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

export interface FindingMiniGraph {
  finding_id: string;
  case_id: string;
  finding_type: string;
  counts: { nodes: number; edges: number };
  nodes: GraphNodeRow[];
  edges: GraphEdgeRow[];
  evidence: Record<string, unknown>[];
  direct_vs_derived: string[];
}

/** Backend-generated evidence-derived graph; never a frontend one-hop guess. */
export function findingGraph(
  investigationId: string,
  findingId: string,
): Promise<FindingMiniGraph> {
  return api(
    `/investigations/${encodeURIComponent(investigationId)}/findings/${encodeURIComponent(findingId)}/graph`,
  );
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

/* ------------------------------------------------------------------------- *
 * Investigator reasoning layer
 *
 * The structured answer of `POST /investigate`. Every structural claim is
 * computed by the backend's deterministic orchestrator; the language model
 * only contributes narrative text, which is why `assessment.model` is a
 * separate, clearly-attributable object rather than a "summary" field.
 *
 * Inference labels are the six-value vocabulary the whole console shares:
 * FACT, CORROBORATED_LEAD, LEAD, HYPOTHESIS, COINCIDENCE, DATA_GAP.
 * ------------------------------------------------------------------------- */

export type InferenceLabel =
  | "FACT"
  | "CORROBORATED_LEAD"
  | "LEAD"
  | "HYPOTHESIS"
  | "COINCIDENCE"
  | "DATA_GAP";

export type EvidenceStance = "supports" | "contradicts" | "context";

export type Strength = "INSUFFICIENT" | "WEAK" | "MODERATE" | "STRONG";

/** One openable pointer behind a claim. */
export interface ProvenanceItem {
  kind: string;
  ref: string;
  label: string;
  detail: string | null;
  doc_id: string | null;
  origin_file: string | null;
  row_number: number | null;
  line_start: number | null;
  line_end: number | null;
  content_hash: string | null;
}

export interface EvidenceItem {
  kind: "document" | "record" | "relationship" | "metric" | "note";
  summary: string;
  inference_label: string;
  stance: EvidenceStance;
  provenance: ProvenanceItem[];
}

export interface ResolvedEntity {
  canonical_id: string;
  label: string;
  display_name: string;
  aliases: string[];
  confidence: number;
  matched_by: string;
  entity_keys: string[];
  /** Echoed from the dataset; the investigator layer never infers this. */
  criminal_status: string | null;
  legal_status?: string | null;
  entity_type?: string | null;
  network_role?: string | null;
  case_ids?: string[];
  investigative_relevance?: any;
  analytical_basis?: any;
  resolved: boolean;
  ambiguity_note: string | null;
}

export interface ObservationBlock {
  observation: string;
  interpretation: string;
  assessment: string;
}

export interface RelationshipPath {
  nodes: string[];
  edges: string[];
  description: string;
}

export interface RelationshipFinding {
  kind:
    | "direct"
    | "indirect"
    | "temporal"
    | "repeated"
    | "cross_case"
    | "suspicious"
    | "coincidental";
  entities: string[];
  title: string;
  description: string;
  evidence: EvidenceItem[];
  inference_label: string;
  /** Flat pointers rolled up from `evidence`: one openable list per finding. */
  provenance: ProvenanceItem[];
  path: RelationshipPath | null;
  analysis: ObservationBlock | null;
  /** Deterministic "why was this relationship surfaced?" */
  why?: string | null;
  /** Observed relationship (STRONG/MODERATE/WEAK/INSUFFICIENT). */
  relationship_strength?: string | null;
  /** Evidentiary confidence band (HIGH/MODERATE/LOW/INSUFFICIENT). */
  evidence_strength?: string | null;
  analytical_basis?: AnalyticalBasis | null;
}

export interface StrengthFactors {
  independent_sources: number;
  corroborating_records: number;
  temporal_relevance: string;
  directness: string;
  consistency: string;
  contradiction_level: string;
  entity_certainty: string;
  notes: string[];
}

/** Deterministic graph-analytical signals behind a finding. */
export interface AnalyticalBasis {
  degree_centrality?: number | null;
  weighted_degree?: number | null;
  betweenness_centrality?: number | null;
  pagerank?: number | null;
  community_id?: number | string | null;
  community_size?: number | null;
  cross_case_count?: number | null;
  relationship_count?: number | null;
  evidence_count?: number | null;
  source_count?: number | null;
  temporal_relevance?: string | null;
  evidence_convergence?: string | null;
  relationship_strength?: string | null;
  bridge_info?: { own_community?: number | string; bridges_to?: (number | string)[]; bridge_count?: number } | null;
  metrics?: Record<string, unknown>;
  explanations?: Record<string, string>;
}

export interface EvidenceConvergenceAssessment {
  convergence_type: string;
  source_categories: string[];
  independent_source_count: number;
  record_count: number;
  explanation: string;
}

export interface EvidenceStrengthAssessment {
  strength: string;
  basis: string[];
  components: Record<string, unknown>;
  explanation: string;
}

export interface InvestigativeRelevanceAssessment {
  relevance: string;
  score?: number | null;
  basis: string[];
  components: Record<string, unknown>;
  explanation: string;
}

/** One structured finding: the full WHY chain around a surfaced signal. */
export interface StructuredFinding {
  finding_id: string;
  title: string;
  finding_type: string;
  objective: string;
  why: string;
  entities: ResolvedEntity[];
  analytical_basis: AnalyticalBasis | null;
  relationships: RelationshipFinding[];
  patterns: SuspiciousPattern[];
  supporting_evidence: EvidenceItem[];
  contradictory_evidence: EvidenceItem[];
  alternative_explanations: string[];
  assessment: Record<string, unknown>;
  data_gaps: DataGap[];
  focused_graph: FocusedGraph;
  next_investigative_direction: string;
}

export interface SuspiciousPattern {
  kind: string;
  title: string;
  explanation: string;
  entities: string[];
  entity_keys: string[];
  cases: string[];
  time_range: Record<string, string | null>;
  evidence: EvidenceItem[];
  inference_label: string;
  strength: Strength;
  strength_factors: StrengthFactors;
  contradictions_considered: string[];
  innocent_alternatives: string[];
  excluded: boolean;
  exclusion_reason: string | null;
  provenance: ProvenanceItem[];
  /** Deterministic "why was this surfaced?" — the mechanism, never a verdict. */
  why?: string | null;
  analytical_basis?: AnalyticalBasis | null;
  evidence_convergence?: EvidenceConvergenceAssessment | null;
  evidence_strength?: EvidenceStrengthAssessment | null;
  investigative_relevance?: InvestigativeRelevanceAssessment | null;
  pattern_type?: string | null;
  disclaimer?: string | null;
}

export interface Hypothesis {
  id: string;
  statement: string;
  entities: string[];
  supporting: EvidenceItem[];
  contradicting: EvidenceItem[];
  innocent_alternatives: string[];
  inference_label: string;
  strength: Strength;
  strength_factors: StrengthFactors;
  /** Both sides rolled up: supporting and contradicting sources alike. */
  provenance: ProvenanceItem[];
  analysis: ObservationBlock | null;
}

export interface DataGap {
  category: string;
  description: string;
  what_would_help: string;
  inference_label: string;
  entities: string[];
  cases: string[];
}

export interface NextStep {
  action: string;
  rationale: string;
  priority: "high" | "medium" | "low";
  links: Record<string, string[]>;
  /** Empty on purpose for gap-closing steps: the record is not in the data yet. */
  provenance: ProvenanceItem[];
}

/** What the language model contributed — or why it contributed nothing. */
export interface ModelSection {
  available: boolean;
  role: string;
  model: string | null;
  reason: string | null;
  summary: string;
  observation: string;
  interpretation: string;
  assessment: string;
  convergence_note: string;
  caveats: string[];
  suggested_next_actions: string[];
  language_edits: string[];
}

export interface AssessmentSection {
  overall_strength: Strength;
  overall_confidence: number;
  convergence: Record<string, unknown>;
  observation: string;
  interpretation: string;
  assessment: string;
  caveats: string[];
  model: ModelSection;
}

export interface MemorySection {
  investigation_id: string;
  /** What this investigation set out to establish (sticky across turns). */
  objective: string;
  questions_asked: number;
  prior_questions: string[];
  confirmed_facts: string[];
  open_hypotheses: Record<string, unknown>[];
  examined_entities: string[];
  open_gaps: string[];
  unresolved: string[];
  /** What earlier turns recorded, including what they ruled out. */
  contradictions: string[];
  relationships: string[];
  rejected_hypotheses: { id?: string; statement?: string; reason?: string }[];
  prior_findings: string[];
}

export interface ScopeSection {
  mode: "case" | "master" | "person";
  /** Human reading of the scope: "Case C106" or "Master Network". */
  label: string;
  dataset_id: string | null;
  dataset_name: string | null;
  case_id: string | null;
  case_number: string | null;
  case_title: string | null;
  case_ids: string[];
  nodes_considered: number;
  edges_considered: number;
  documents_considered: number;
}

export interface FocusedGraphNode {
  key: string;
  label: string;
  name: string;
  focus: boolean;
}

export interface FocusedGraphEdge {
  source: string;
  target: string;
  rel_type: string;
}

export interface FocusedGraph {
  nodes?: FocusedGraphNode[];
  edges?: FocusedGraphEdge[];
  truncated?: boolean;
}

export interface TimelineEntry {
  ts?: string;
  timestamp?: string;
  label?: string;
  summary?: string;
  rel_type?: string;
  source?: string;
  doc_id?: string | null;
  [key: string]: unknown;
}

export interface InvestigatorResponse {
  question: string;
  objective: string;
  investigation_id: string;
  scope: ScopeSection;
  entities: ResolvedEntity[];
  facts: EvidenceItem[];
  relationships: RelationshipFinding[];
  patterns: SuspiciousPattern[];
  hypotheses: Hypothesis[];
  alternative_explanations: string[];
  assessment: AssessmentSection;
  gaps: DataGap[];
  next_steps: NextStep[];
  timeline: TimelineEntry[];
  focused_graph: FocusedGraph;
  provenance: ProvenanceItem[];
  memory: MemorySection | null;
  timing_ms: Record<string, number>;
  structured_findings?: StructuredFinding[];
  analytical_basis?: any;
  investigative_relevance?: any;
  evidence_strength?: any;
  evidence_convergence?: any;
  silent_intermediaries?: SilentIntermediaryFinding[];
  data_quality?: any[];
  validation_notes?: string[];
}

/** Low-visibility, structurally important intermediary — never "silent criminal". */
export interface SilentIntermediaryFinding {
  entity_id: string;
  display_name: string;
  entity_type: string;
  why_surfaced: string;
  analytical_basis: AnalyticalBasis;
  network_role: string;
  investigative_relevance: string;
  evidence_strength: string;
  supporting_evidence: EvidenceItem[];
  community_bridges: (number | string)[];
  cross_case_bridges: string[];
  disclaimer: string;
}

export interface InvestigatePayload {
  question: string;
  case_id?: string | null;
  investigation_id?: string | null;
  objective?: string | null;
  max_patterns?: number;
  include_excluded?: boolean;
}

/**
 * Run an investigation question.
 *
 * A POST is deliberately not deduplicated by ``api()`` (only GETs are), so
 * two identical questions asked in sequence are two genuine investigations.
 */
export function investigate(payload: InvestigatePayload): Promise<InvestigatorResponse> {
  return api("/investigate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question: payload.question,
      case_id: payload.case_id ?? null,
      investigation_id: payload.investigation_id ?? null,
      objective: payload.objective ?? null,
      max_patterns: payload.max_patterns ?? 25,
      include_excluded: payload.include_excluded ?? true,
    }),
  });
}

export interface PatternDetectionResult {
  dataset_id: string;
  dataset_name: string | null;
  mode: "case" | "master";
  scope_label: string;
  case_id: string | null;
  case_number: string | null;
  case_ids: string[];
  nodes_considered: number;
  edges_considered: number;
  patterns: SuspiciousPattern[];
  count: number;
  excluded_count: number;
}

/**
 * Structured pattern detection without a question — the same detectors the
 * orchestrator runs, exposed on their own so the workspace can show the
 * signal list before anyone types anything.
 */
export function investigationPatterns(opts: {
  caseId?: string | null;
  maxPatterns?: number;
  includeExcluded?: boolean;
} = {}): Promise<PatternDetectionResult> {
  const params = new URLSearchParams();
  if (opts.caseId) params.set("case_id", opts.caseId);
  params.set("max_patterns", String(opts.maxPatterns ?? 25));
  params.set("include_excluded", String(opts.includeExcluded ?? true));
  return api(`/investigate/patterns?${params.toString()}`);
}

export interface InvestigationSessionPayload {
  investigation_id: string;
  dataset_id: string;
  case_id: string | null;
  scope: string;
  title: string;
  created_at: string | null;
  updated_at: string | null;
  memory: MemorySection;
  objective: string;
  questions: string[];
  contradictions: string[];
}

/** Read a thread's memory (dataset-pinned) so a page reload can resume it. */
export function investigationSession(
  investigationId: string,
): Promise<InvestigationSessionPayload> {
  return api(`/investigate/sessions/${encodeURIComponent(investigationId)}`);
}

export interface InvestigationJob {
  id: string;
  dataset_id: string | null;
  case_id: string | null;
  investigation_id: string | null;
  question: string;
  objective: string | null;
  status: string;
  stage: string;
  progress_pct: number;
  message: string;
  steps: { stage: string; message: string; at: string; status: string }[];
  result: { response?: InvestigatorResponse; status?: string; [k: string]: any };
  error: string | null;
  requested_by: string | null;
  created_at: string | null;
  updated_at: string | null;
  finished_at: string | null;
  terminal: boolean;
}

export function startInvestigationJob(payload: InvestigatePayload): Promise<InvestigationJob> {
  return api("/investigate/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question: payload.question,
      case_id: payload.case_id ?? null,
      investigation_id: payload.investigation_id ?? null,
      objective: payload.objective ?? null,
      max_patterns: payload.max_patterns ?? 25,
      include_excluded: payload.include_excluded ?? true,
    }),
  });
}

export function getInvestigationJob(jobId: string): Promise<InvestigationJob> {
  return api(`/investigate/jobs/${encodeURIComponent(jobId)}`);
}

export function getInvestigationJobWsUrl(jobId: string): string {
  const token = localStorage.getItem("crimelink.access") || "";
  const base = `/api/v1/jobs/ws/investigation/${encodeURIComponent(jobId)}`;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.host;
  return `${proto}://${host}${base}?token=${encodeURIComponent(token)}`;
}

export function getInvestigationJobWsUrlAlt(jobId: string): string {
  // Alternative route under /investigate/jobs/ws/job/
  const token = localStorage.getItem("crimelink.access") || "";
  const base = `/api/v1/investigate/jobs/ws/job/${encodeURIComponent(jobId)}`;
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const host = window.location.host;
  return `${proto}://${host}${base}?token=${encodeURIComponent(token)}`;
}

// ---------------------------------------------------------------------------
// Cases registry
// ---------------------------------------------------------------------------

export interface CaseSummary {
  id: string;
  case_number: string;
  title: string;
  jurisdiction_id: string;
  status?: string;
  dataset_id?: string;
}

export function listCases(): Promise<{ items: CaseSummary[] }> {
  return api("/cases");
}

// ---------------------------------------------------------------------------
// Network analysis — the explicit three-scope graph surface.
//
// MASTER NETWORK  = the complete active-dataset graph (cross-case).
// CASE NETWORK    = one case's master graph.
// PERSON NETWORK  = one person's neighbourhood, cross-case, active dataset.
//
// Nothing auto-runs: the workspace posts /investigate/network-analysis and
// polls the returned job exactly like a question investigation.  The graph
// itself is rendered from the real graph endpoints below — never fabricated.
// ---------------------------------------------------------------------------

export type NetworkScopeMode = "master" | "case" | "person";

/** The complete active-dataset graph (same row shape as a case graph). */
export interface MasterGraph extends CaseGraph {
  mode: "master";
  case_ids: string[];
}

export function masterGraph(
  opts: {
    includeStaging?: boolean;
    labels?: string[];
    relTypes?: string[];
    limit?: number;
  } = {},
): Promise<MasterGraph> {
  const params = new URLSearchParams();
  if (opts.includeStaging) params.set("include_staging", "true");
  if (opts.labels?.length) params.set("labels", opts.labels.join(","));
  if (opts.relTypes?.length) params.set("rel_types", opts.relTypes.join(","));
  if (opts.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return api(`/graph/master${qs ? `?${qs}` : ""}`);
}

/** Shared entity connecting cases in the Master Case Network. */
export interface MasterCaseSharedEntity {
  provenance_key: string;
  name: string;
  label: string;
  is_criminal: boolean;
  criminal_status: string | null;
  source_doc_ids: string[];
  evidence?: NodeEvidence | null;
}

/** Openable evidence pointer supporting a case connection. */
export interface MasterCaseSupportingEvidence {
  source_doc_id?: string | null;
  category: string;
  pointer?: NodeEvidence | null;
  label: string;
}

/** A CASE node in the Master Case Network (always a circle). */
export interface MasterCaseNode {
  id: string;
  provenance_key: string;
  case_number: string;
  title: string;
  status: string;
  jurisdiction_id: string;
  label: "CASE";
  is_criminal: false;
  document_count: number;
  entity_count: number;
  related_cases_count: number;
  connected_cases: string[];
  shared_entities: MasterCaseSharedEntity[];
}

/** An evidence-backed connection between two cases. */
export interface MasterCaseEdge {
  id: string;
  source: string;
  target: string;
  source_case_number: string;
  target_case_number: string;
  strength: "STRONG" | "MODERATE" | "WEAK";
  shared_entities: MasterCaseSharedEntity[];
  shared_entity_count: number;
  relationship_count: number;
  evidence_count: number;
  source_categories: string[];
  temporal_overlap: string;
  why: string;
  analytical_basis: string[];
  supporting_evidence: MasterCaseSupportingEvidence[];
  contradictory_evidence: string;
  data_gaps: string[];
  next_direction: string;
}

/** Complete response from GET /graph/master/case-network. */
export interface MasterCaseNetworkResult {
  mode: "master_case";
  dataset_id: string | null;
  counts: {
    cases: number;
    connections: number;
  };
  nodes: MasterCaseNode[];
  edges: MasterCaseEdge[];
  empty_reason?: string;
}

export function masterCaseNetwork(
  opts: { includeStaging?: boolean } = {},
): Promise<MasterCaseNetworkResult> {
  const params = new URLSearchParams();
  if (opts.includeStaging) params.set("include_staging", "true");
  const qs = params.toString();
  return api(`/graph/master/case-network${qs ? `?${qs}` : ""}`);
}

// ---------------------------------------------------------------------------
// PERSON → PERSON relationship network (the primary investigator graph).
//
// Nodes are PERSON and nothing else.  Phones, bank accounts, vehicles,
// locations, organisations and documents are walked server-side to *establish*
// and *evidence* an edge between two people; they never come back as nodes.
// The full entity graph stays available through masterGraph() for the ENTITY
// NETWORK view, which is a different graph, not a re-titled copy of this one.
// ---------------------------------------------------------------------------

/** Relationship types the person network can emit, in display order. */
export const PERSON_RELATIONSHIP_TYPES = [
  "COMMUNICATION",
  "FINANCIAL_LINK",
  "NAMED_ACCOMPLICE",
  "ARRESTED_WITH",
  "FAMILY_RELATIVE",
  "SHARED_VEHICLE",
  "SHARED_ACCOUNT",
  "SHARED_PHONE",
  "SHARED_ADDRESS",
  "SHARED_ORGANIZATION",
  "SHARED_IDENTIFIER",
  "SOCIAL_LINK",
  "KNOWN_ASSOCIATION",
  "EVIDENCE_SUPPORTED",
] as const;

export type PersonRelationshipType = (typeof PERSON_RELATIONSHIP_TYPES)[number];

/** One record behind a person-to-person edge (the evidence, not a node). */
export interface RelationshipSupportingItem {
  kind:
    | "PHONE"
    | "BANK_ACCOUNT"
    | "VEHICLE"
    | "LOCATION"
    | "ORGANIZATION"
    | "COMMUNICATION"
    | "TRANSACTION"
    | "DIRECT_RECORD";
  relationship_type: PersonRelationshipType;
  label: string;
  ref: string;
  detail?: string;
  rel_types: string[];
  source_doc_ids: string[];
  case_ids: string[];
  confidence: number;
  evidence?: NodeEvidence | null;
  via?: string[];
  via_labels?: string[];
  properties?: Record<string, unknown>;
}

/** A PERSON node in the relationship graph. */
export interface RelationshipPersonNode extends GraphNodeRow {
  /** Source-derived role (SUSPECT / WITNESS / …); never a criminality score. */
  role?: string | null;
  relationship_count: number;
  evidence_count: number;
}

/** One aggregated person-to-person relationship. */
export interface RelationshipEdge {
  id: string;
  source: string;
  target: string;
  relationship_type: PersonRelationshipType;
  label: string;
  relationship_types: PersonRelationshipType[];
  relationship_type_counts: Record<string, number>;
  supporting_items: RelationshipSupportingItem[];
  supporting_item_count: number;
  supporting_kinds: string[];
  rel_types: string[];
  evidence_count: number;
  source_doc_ids: string[];
  case_ids: string[];
  cross_case: boolean;
  strength: "STRONG" | "MODERATE" | "WEAK";
  confidence: number;
}

export interface RelationshipNetworkResult {
  mode: "master_relationships" | "case_relationships";
  view: "PERSON_NETWORK";
  node_types: string[];
  dataset_id?: string | null;
  case_id?: string | null;
  case_ids: string[];
  counts: {
    persons: number;
    relationships: number;
    relationships_total: number;
    by_relationship_type: Record<string, number>;
    by_relationship_type_total: Record<string, number>;
    persons_total: number;
    persons_linked: number;
    confirmed_criminals: number;
    supporting_items: number;
  };
  truncated: boolean;
  limit: number | null;
  filters: { relationship_types: string[]; min_evidence: number };
  suppressed_shared_entities: Record<string, number>;
  nodes: RelationshipPersonNode[];
  edges: RelationshipEdge[];
  empty_reason?: string | null;
}

export interface RelationshipNetworkQuery {
  caseId?: string;
  limit?: number;
  includeIsolated?: boolean;
  relationshipTypes?: string[];
  minEvidence?: number;
}

export function relationshipNetwork(
  query: RelationshipNetworkQuery = {},
): Promise<RelationshipNetworkResult> {
  const params = new URLSearchParams();
  if (query.limit) params.set("limit", String(query.limit));
  if (query.includeIsolated) params.set("include_isolated", "true");
  if (query.relationshipTypes?.length) {
    params.set("relationship_types", query.relationshipTypes.join(","));
  }
  if (query.minEvidence && query.minEvidence > 1) {
    params.set("min_evidence", String(query.minEvidence));
  }
  const qs = params.toString();
  const base = query.caseId
    ? `/graph/cases/${encodeURIComponent(query.caseId)}/relationships`
    : "/graph/master/relationships";
  return api(`${base}${qs ? `?${qs}` : ""}`);
}

/** The evidence behind one person-to-person edge. */
export interface RelationshipEvidenceResult {
  mode: "relationship_evidence";
  case_id?: string | null;
  case_ids: string[];
  source: string;
  target: string;
  source_person: GraphNodeRow;
  target_person: GraphNodeRow;
  relationship: RelationshipEdge | null;
  supporting_items: RelationshipSupportingItem[];
  supporting_item_count: number;
  empty_reason?: string | null;
}

export function relationshipEvidence(
  source: string,
  target: string,
): Promise<RelationshipEvidenceResult> {
  const params = new URLSearchParams({ source, target });
  return api(`/graph/master/relationship-evidence?${params.toString()}`);
}

/** One selectable person target in the active dataset (cross-case). */
export interface MasterPersonTarget extends PersonTarget {
  case_ids: string[];
  criminal_status: string | null;
  is_criminal?: boolean;
}

export function masterPersons(): Promise<{
  mode: "master";
  case_ids: string[];
  total_persons: number;
  items: MasterPersonTarget[];
}> {
  return api("/graph/master/persons");
}

/** A person's neighbourhood over the active dataset (cross-case). */
export interface MasterPersonNetwork extends Omit<PersonNetwork, "case_id"> {
  mode: "master";
  case_ids: string[];
  person_case_ids: string[];
}

export function masterPersonNetwork(
  personKey: string,
  depth: number = DEFAULT_NETWORK_DEPTH,
): Promise<MasterPersonNetwork> {
  const hops = Math.max(1, Math.floor(Number(depth) || DEFAULT_NETWORK_DEPTH));
  const params = new URLSearchParams({ depth: String(hops) });
  return api(
    `/graph/master/person/${encodeURIComponent(personKey)}?${params.toString()}`,
  );
}

/** One ranked metric row (structural measure, never a criminality score). */
export interface NetworkMetricRow {
  key: string;
  name: string;
  label: string;
  value: number;
  case_count?: number;
  is_criminal: boolean;
}

export interface NetworkCommunity {
  id: number;
  size: number;
  top_members: { key: string; name: string; label: string }[];
}

export interface NetworkCrossCaseRow {
  key: string;
  name: string;
  label: string;
  case_count: number;
  case_ids: string[];
  betweenness: number;
  is_criminal: boolean;
}

export interface NetworkAnalysisAnalysis {
  mode: NetworkScopeMode;
  scope_label: string;
  case_ids: string[];
  case_number: string | null;
  case_title: string | null;
  person_key: string | null;
  person_name: string | null;
  graph: { nodes: number; edges: number; communities: number };
  metrics: {
    betweenness: NetworkMetricRow[];
    degree: NetworkMetricRow[];
    weighted_degree: NetworkMetricRow[];
    pagerank: NetworkMetricRow[];
    explanations: Record<string, string>;
  };
  communities: NetworkCommunity[];
  cross_case: NetworkCrossCaseRow[];
}

export interface NetworkAnalysisResult {
  status: string;
  response: InvestigatorResponse;
  analysis: NetworkAnalysisAnalysis;
}

export interface NetworkAnalysisRequest {
  mode: NetworkScopeMode;
  case_id?: string | null;
  person_key?: string | null;
  max_patterns?: number;
  include_excluded?: boolean;
}

export function startNetworkAnalysis(payload: NetworkAnalysisRequest): Promise<InvestigationJob> {
  return api("/investigate/network-analysis", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode: payload.mode,
      case_id: payload.case_id ?? null,
      person_key: payload.person_key ?? null,
      max_patterns: payload.max_patterns ?? 25,
      include_excluded: payload.include_excluded ?? true,
    }),
  });
}

/** Extract a finished job's network-analysis payload, or null if incomplete. */
export function networkAnalysisFromJob(job: InvestigationJob | null | undefined): NetworkAnalysisResult | null {
  if (!job) return null;
  const result = job.result as { response?: InvestigatorResponse; analysis?: NetworkAnalysisAnalysis; status?: string } | undefined;
  if (result && result.response && result.analysis) {
    return {
      status: result.status ?? job.status,
      response: result.response,
      analysis: result.analysis,
    };
  }
  return null;
}

// ---------------------------------------------------------------------------
// Sprint 2 — Professional Investigation Workflow
// ---------------------------------------------------------------------------

export interface GlobalSearchResult {
  query: string;
  categories: {
    entities: any[];
    cases: any[];
    evidence: any[];
    documents: any[];
    patterns: any[];
    locations: any[];
  };
  counts: Record<string, number>;
  total: number;
}

export function globalSearch(q: string, limit = 20): Promise<GlobalSearchResult> {
  return api(`/search/global?q=${encodeURIComponent(q)}&limit=${limit}`);
}

export interface CaseDashboard {
  header: {
    id: string;
    case_number: string;
    title: string;
    status: string;
    jurisdiction_id: string;
    dataset_id: string | null;
    created_at: string | null;
    updated_at: string | null;
    closed_at: string | null;
    description: string;
  };
  stats: {
    entities: number;
    entities_by_label: Record<string, number>;
    relationships: number;
    relationships_by_type: Record<string, number>;
    evidence: number;
    documents: number;
    patterns: number;
    unresolved: number;
  };
  intelligence: {
    high_priority: any[];
    gaps: any[];
    unresolved: any[];
    patterns: any[];
    recent_activity: any[];
  };
}

export function caseDashboard(caseId: string): Promise<CaseDashboard> {
  return api(`/cases/${encodeURIComponent(caseId)}/dashboard`);
}

export interface EnhancedTimelineEvent {
  event_key: string;
  timestamp: string | null;
  type: string;
  entity: string | null;
  related_entities: string[];
  participants: any[];
  location: string | null;
  evidence: string[];
  evidence_doc_ids: string[];
  source_doc_id: string | null;
  case_id: string;
  source: string | null;
  confidence: number;
  provenance: any;
  name: string;
  description: string;
  event_type: string | null;
  at: string | null;
}

export interface EnhancedTimelineResponse {
  case_id: string;
  events: EnhancedTimelineEvent[];
  count: number;
}

export function enhancedTimeline(
  caseId: string,
  opts: {
    from_ts?: string;
    to_ts?: string;
    participant?: string;
    event_type?: string;
    location?: string;
    evidence_type?: string;
    entity?: string;
    limit?: number;
  } = {}
): Promise<EnhancedTimelineResponse> {
  const params = new URLSearchParams();
  if (opts.from_ts) params.set("from_ts", opts.from_ts);
  if (opts.to_ts) params.set("to_ts", opts.to_ts);
  if (opts.participant) params.set("participant", opts.participant);
  if (opts.event_type) params.set("event_type", opts.event_type);
  if (opts.location) params.set("location", opts.location);
  if (opts.evidence_type) params.set("evidence_type", opts.evidence_type);
  if (opts.entity) params.set("entity", opts.entity);
  if (opts.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return api(`/cases/${encodeURIComponent(caseId)}/timeline${qs ? `?${qs}` : ""}`);
}

export interface TimelineAnalyzeRequest {
  from_ts?: string | null;
  to_ts?: string | null;
  question?: string | null;
}

export interface TimelineAnalyzeResponse {
  case_id: string;
  window: { from_ts: string | null; to_ts: string | null };
  events: EnhancedTimelineEvent[];
  context_timeline: any[];
  counts: { events: number; entities: number; relationships: number };
  ai_analysis: any | null;
}

export function analyzeTimelineWindow(
  caseId: string,
  payload: TimelineAnalyzeRequest
): Promise<TimelineAnalyzeResponse> {
  return api(`/cases/${encodeURIComponent(caseId)}/timeline/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export interface PatternInvestigation {
  pattern: {
    id: string;
    case_id: string;
    pattern_type: string;
    explanation: string;
    confidence: number;
    status: string;
    entity_keys: string[];
    evidence_level: string;
    analytical_basis: string[];
    contradictory_evidence: string;
    data_gaps: string[];
  };
  entities: any[];
  relationships: any[];
  related_cases: any[];
  evidence: { doc_ids: string[]; count: number };
  graph: { nodes: any[]; edges: any[]; center: string | null };
  counts: { entities: number; relationships: number; related_cases: number; evidence: number };
  provenance: any;
}

export function investigatePattern(patternId: string): Promise<PatternInvestigation> {
  return api(`/patterns/${encodeURIComponent(patternId)}/investigation`);
}

export interface AttentionItem {
  id: string;
  type: string;
  case_id: string;
  severity: string;
  title: string;
  description: string;
  timestamp: string | null;
  link: { kind: string; case_id?: string; pattern_id?: string; finding_id?: string; document_id?: string; entity_key?: string; resolution_id?: string };
}

export interface AttentionCenter {
  categories: {
    critical: AttentionItem[];
    investigation: AttentionItem[];
    data_quality: AttentionItem[];
    evidence: AttentionItem[];
  };
  counts: { critical: number; investigation: number; data_quality: number; evidence: number };
  total: number;
}

export function attentionCenter(): Promise<AttentionCenter> {
  return api(`/attention`);
}

export function attentionCounts(): Promise<{ counts: Record<string, number>; total: number }> {
  return api(`/attention/counts`);
}
