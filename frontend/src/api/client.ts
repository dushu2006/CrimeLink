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

let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler;
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

async function refreshSession(): Promise<boolean> {
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

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response = await raw(path, init);
  if (response.status === 401 && tokenStore.refresh) {
    const renewed = await refreshSession();
    if (renewed) {
      response = await raw(path, init);
    } else {
      tokenStore.clear();
      onUnauthorized?.();
      throw new ApiError(401, "session_expired", "Your session has expired. Please sign in again.");
    }
  }
  const payload = await parse(response);
  if (!response.ok) {
    const { code, message, fields } = messageFrom(payload, response.status);
    throw new ApiError(response.status, code, message, fields);
  }
  return payload as T;
}

/**
 * Fetch a binary artefact (the watermarked PDF brief) with the session token and
 * hand it to the browser as a download.
 *
 * A plain <a download> cannot carry the Authorization header, so the file is
 * pulled with the session attached and turned into an object URL.
 */
export async function download(path: string, filename: string): Promise<void> {
  const response = await fetch(`/api/v1${path}`, { headers: authHeaders() });
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

/** Live processing status for a case (PRD: "Stage 3/6 — NLP extraction"). */
export function jobSocket(caseId: string, onMessage: (event: unknown) => void): () => void {
  const token = tokenStore.access;
  if (!token) return () => undefined;
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(
    `${scheme}://${window.location.host}/api/v1/jobs/ws/${caseId}?token=${encodeURIComponent(token)}`,
  );
  socket.onmessage = (event) => {
    try {
      onMessage(JSON.parse(event.data));
    } catch {
      /* ignore malformed frames */
    }
  };
  return () => socket.close();
}
