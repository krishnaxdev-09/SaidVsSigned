// API client (doc 01 §4 step 10): in-memory access token, one refresh retry on 401 (httpOnly cookie),
// Idempotency-Key on resource-creating POSTs (doc 11 §2). The caller keeps the key across retries of one action.

export class ApiError extends Error {
  constructor(public status: number, public code: string, detail: string) {
    super(detail);
  }
}

let accessToken: string | null = null;
export const setAccessToken = (t: string | null) => { accessToken = t; };
export const hasAccessToken = () => accessToken !== null;

type Fetch = typeof fetch;
let fetchImpl: Fetch = (...a) => fetch(...a);
export const _setFetch = (f: Fetch) => { fetchImpl = f; };

export const newIdempotencyKey = () => crypto.randomUUID();

// One refresh at a time: refresh tokens rotate, so two concurrent refreshes would present the old token twice and the
// server would treat it as reuse and revoke the session (seen on page reload under React StrictMode).
let inFlight: Promise<boolean> | null = null;
function refresh(): Promise<boolean> {
  inFlight ??= (async () => {
    try {
      const r = await fetchImpl("/api/v1/auth/refresh", { method: "POST", credentials: "same-origin" });
      if (!r.ok) { accessToken = null; return false; }
      accessToken = (await r.json()).access_token;
      return true;
    } finally {
      inFlight = null;
    }
  })();
  return inFlight;
}

export async function api<T>(path: string, opts: { method?: string; body?: unknown; idempotencyKey?: string } = {},
                             retried = false): Promise<T> {
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["content-type"] = "application/json";
  if (accessToken) headers.authorization = `Bearer ${accessToken}`;
  if (opts.idempotencyKey) headers["idempotency-key"] = opts.idempotencyKey;
  const r = await fetchImpl(path, { method: opts.method ?? "GET", headers, credentials: "same-origin",
                                    body: opts.body === undefined ? undefined : JSON.stringify(opts.body) });
  if (r.status === 401 && !retried && !path.startsWith("/api/v1/auth/")) {
    if (await refresh()) return api<T>(path, opts, true);
  }
  if (r.status === 204) return undefined as T;
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, data.code ?? "internal_error", data.detail ?? r.statusText);
  return data as T;
}

// Multipart upload (doc 02 §4). The browser sets the multipart boundary; the caller keeps the Idempotency-Key
// across retries of one upload.
export async function upload<T>(path: string, method: "POST" | "PUT", form: FormData, idempotencyKey: string,
                                retried = false): Promise<T> {
  const headers: Record<string, string> = { "idempotency-key": idempotencyKey };
  if (accessToken) headers.authorization = `Bearer ${accessToken}`;
  const r = await fetchImpl(path, { method, headers, credentials: "same-origin", body: form });
  if (r.status === 401 && !retried && await refresh()) return upload<T>(path, method, form, idempotencyKey, true);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, data.code ?? "internal_error", data.detail ?? r.statusText);
  return data as T;
}

export async function login(email: string, password: string) {
  const out = await api<{ access_token: string }>("/api/v1/auth/login", { method: "POST", body: { email, password } });
  setAccessToken(out.access_token);
}
export const tryRestore = () => refresh();
export async function logout() { await api("/api/v1/auth/logout", { method: "POST" }); setAccessToken(null); }

// Raw binary PUT (live microphone chunks). Safe to retry: the server replaces a chunk re-sent with the same number.
export async function putBlob<T>(path: string, blob: Blob, retried = false): Promise<T> {
  const headers: Record<string, string> = { "content-type": "application/octet-stream" };
  if (accessToken) headers.authorization = `Bearer ${accessToken}`;
  const r = await fetchImpl(path, { method: "PUT", headers, credentials: "same-origin", body: blob });
  if (r.status === 401 && !retried && await refresh()) return putBlob<T>(path, blob, true);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, data.code ?? "internal_error", data.detail ?? r.statusText);
  return data as T;
}
