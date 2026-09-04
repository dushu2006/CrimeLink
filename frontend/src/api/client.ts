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

async function raw(path: string, init: RequestInit = {}, token?: string | null): Promise<Response> {
  const headers = new Headers(init.headers);
  const bearer = token ?? tokenStore.access;
  if (bearer) headers.set("Authorization", `Bearer ${bearer}`);
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
    if (!response.ok) return false;
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
 * close — backend restart, network blip — is retried with a bounded backoff:
 * enough to ride out a redeploy, never enough to become a retry storm when
 * the server is genuinely gone or the session cannot be repaired.
 */
const WS_MAX_RETRY_ATTEMPTS = 5;
const WS_MAX_RETRY_DELAY_MS = 15_000;
// Renewals that keep failing to produce an accepted socket before the
// supervisor gives up instead of looping refresh -> connect forever.
const WS_MAX_AUTH_RETRIES = 2;

export function jobSocket(caseId: string, onMessage: (event: unknown) => void): () => void {
  let stopped = false;
  let socket: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let attempts = 0; // consecutive non-auth failures since the last open
  let authRetries = 0; // 4401 cycles since the last successful open

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
      if (attempts >= WS_MAX_RETRY_ATTEMPTS) return;
      const delay = Math.min(1000 * 2 ** attempts, WS_MAX_RETRY_DELAY_MS);
      attempts += 1;
      timer = setTimeout(connect, delay);
    };
  };

  connect();

  return () => {
    stopped = true;
    if (timer !== null) clearTimeout(timer);
    if (socket) {
      socket.onclose = null;
      socket.onmessage = null;
      socket.close();
      socket = null;
    }
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
  target: GraphNodeRow;
  depth: number;
  truncated: boolean;
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

export function personNetwork(
  caseId: string,
  personKey: string,
  depth: 1 | 2 | 3 = 1,
): Promise<PersonNetwork> {
  return api(
    `/cases/${caseId}/network/${encodeURIComponent(personKey)}?depth=${depth}`,
  );
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
