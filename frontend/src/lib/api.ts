"use client";

/** API client: same-origin /backend proxy → FastAPI.
 *  Auth: Supabase access token when configured, else dev email header. */

const API = "/backend/api/v1";

function authHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const token = localStorage.getItem("access_token");
  if (token) return { Authorization: `Bearer ${token}` };
  const devEmail = localStorage.getItem("dev_email");
  if (devEmail) return { "X-Dev-Email": devEmail };
  return {};
}

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(code: string, message: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(init.headers || {}),
    },
  });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = body?.error || {};
    throw new ApiError(err.code || "unknown", err.message || res.statusText, res.status);
  }
  return body as T;
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) }),
  put: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body ?? {}) }),
  patch: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body ?? {}) }),
  del: <T,>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: async <T,>(path: string, formData: FormData) => {
    const res = await fetch(`${API}${path}`, {
      method: "POST",
      body: formData,
      headers: authHeaders(),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = body?.error || {};
      throw new ApiError(err.code || "unknown", err.message || res.statusText, res.status);
    }
    return body as T;
  },
};

export const fmtMoney = (value?: string | number | null, currency = "EGP", locale = "ar") => {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  return new Intl.NumberFormat(locale === "ar" ? "ar-EG" : "en-US", {
    style: "currency", currency, maximumFractionDigits: 0,
  }).format(n);
};

export const fmtDate = (iso?: string | null, locale = "ar") =>
  iso
    ? new Intl.DateTimeFormat(locale === "ar" ? "ar-EG" : "en-US", {
        dateStyle: "medium", timeStyle: "short",
      }).format(new Date(iso))
    : "—";
