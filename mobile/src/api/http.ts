/**
 * cookie-aware HTTP client for the RPG Roleplay FastAPI backend.
 *
 * The backend authenticates with an HTTP-only `rpg_session` cookie (no bearer token).
 * RN's implicit cookie jar is unreliable across app restarts and arbitrary self-hosted
 * hosts, so we capture `set-cookie` on auth responses and re-attach `Cookie:` manually.
 */
import { getServerUrl, getSessionCookie, setSessionCookie } from "./storage";

export class ApiError extends Error {
  code: string;
  status: number;
  payload: unknown;
  constructor(code: string, status: number, message: string, payload?: unknown) {
    super(message);
    this.code = code || "error";
    this.status = status;
    this.payload = payload;
  }
}

let onAuthExpired: (() => void) | null = null;
export function setAuthExpiredHandler(fn: (() => void) | null) {
  onAuthExpired = fn;
}

/**
 * Extract rpg_session from each Set-Cookie header individually (RFC 6265 §5.2).
 * Joining headers with "; " is unsafe — attribute/value boundaries blur.
 */
const SESSION_RE = /^\s*rpg_session=([^;]+)/i;

async function captureCookie(headers: Headers): Promise<void> {
  const anyHeaders = headers as unknown as { getSetCookie?: () => string[] };
  const setCookies: string[] =
    typeof anyHeaders.getSetCookie === "function" ? anyHeaders.getSetCookie() : [];

  // Fallback: some RN fetch implementations fold Set-Cookie into a single header
  if (setCookies.length === 0) {
    const raw = headers.get("set-cookie") || "";
    if (raw) setCookies.push(raw);
  }

  for (const sc of setCookies) {
    const m = SESSION_RE.exec(sc);
    if (m && m[1]) {
      await setSessionCookie(`rpg_session=${m[1]}`);
      return; // first match wins
    }
  }
}

export async function baseUrl(): Promise<string> {
  const u = await getServerUrl();
  if (!u) throw new ApiError("no_server", 0, "未配置服务器地址");
  return u;
}

async function buildHeaders(extra?: Record<string, string>): Promise<Record<string, string>> {
  const headers: Record<string, string> = { Accept: "application/json", ...(extra || {}) };
  const cookie = await getSessionCookie();
  if (cookie) headers.Cookie = cookie;
  return headers;
}

type RequestOpts = {
  method?: string;
  body?: unknown;
  timeoutMs?: number;
};

async function request<T = unknown>(path: string, opts: RequestOpts = {}): Promise<T> {
  const base = await baseUrl();
  const url = path.startsWith("http") ? path : base + path;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opts.timeoutMs ?? 20000);

  const headers = await buildHeaders();
  let body: BodyInit | undefined;
  if (opts.body instanceof FormData) {
    body = opts.body;
  } else if (opts.body != null) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }

  let res: Response;
  try {
    res = await fetch(url, {
      method: opts.method || "GET",
      headers,
      body,
      signal: controller.signal,
    });
  } catch (e: any) {
    clearTimeout(timer);
    throw new ApiError("network", 0, "网络异常：" + (e?.message || "无法连接服务器"), { url });
  }
  clearTimeout(timer);

  await captureCookie(res.headers);

  const ct = res.headers.get("content-type") || "";
  const isJson = ct.includes("application/json");
  const payload: any = isJson ? await res.json().catch(() => null) : await res.text();

  if (!res.ok) {
    if (res.status === 401) {
      await setSessionCookie(null);
      onAuthExpired?.();
    }
    const detail =
      (payload && (payload.error || payload.detail)) ||
      res.statusText ||
      `请求失败 (HTTP ${res.status})`;
    const msg = Array.isArray(detail)
      ? detail.map((d: any) => d?.msg || d).join("；")
      : typeof detail === "object"
        ? JSON.stringify(detail)
        : String(detail);
    throw new ApiError(payload?.code || "http", res.status, msg, payload);
  }
  return payload as T;
}

export const http = {
  get: <T = unknown>(path: string, query?: Record<string, unknown>) => {
    let p = path;
    if (query) {
      const usp = new URLSearchParams();
      for (const [k, v] of Object.entries(query)) {
        if (v === undefined || v === null || v === "") continue;
        usp.set(k, String(v));
      }
      const qs = usp.toString();
      if (qs) p += (p.includes("?") ? "&" : "?") + qs;
    }
    return request<T>(p, { method: "GET" });
  },
  post: <T = unknown>(path: string, body?: unknown, timeoutMs?: number) =>
    request<T>(path, { method: "POST", body: body ?? {}, timeoutMs }),
  patch: <T = unknown>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body ?? {} }),
  put: <T = unknown>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body ?? {} }),
  del: <T = unknown>(path: string, body?: unknown) =>
    request<T>(path, { method: "DELETE", body: body ?? {} }),
  postForm: <T = unknown>(path: string, form: FormData, timeoutMs = 60000) =>
    request<T>(path, { method: "POST", body: form, timeoutMs }),
};
