// Central API layer. Components never call fetch() directly; they use the
// typed resource modules built on this client.

import { FETCH_CREDENTIALS, authHeaders, notifyAuthRequired } from "@/lib/auth";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

async function parseError(res: Response): Promise<never> {
  if (res.status === 401) notifyAuthRequired();
  let detail = `Request failed (${res.status})`;
  try {
    const body = await res.json();
    if (body?.detail) {
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    }
  } catch {
    /* non-JSON error body */
  }
  if (res.status === 403) {
    detail = `Your account's role doesn't permit this action (${detail}).`;
  }
  throw new ApiError(res.status, detail);
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    credentials: FETCH_CREDENTIALS,
    headers: await authHeaders(),
  });
  if (!res.ok) return parseError(res);
  return res.json() as Promise<T>;
}

export async function apiSend<T>(
  path: string,
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  body?: unknown,
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    credentials: FETCH_CREDENTIALS,
    headers: {
      ...(await authHeaders()),
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) return parseError(res);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/** Fetch a file with the caller's credentials (a plain link would carry none). */
export async function apiDownload(path: string): Promise<{ blob: Blob; filename: string | null }> {
  const res = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    credentials: FETCH_CREDENTIALS,
    headers: await authHeaders(),
  });
  if (!res.ok) return parseError(res);
  const disposition = res.headers.get("content-disposition") ?? "";
  const filename = /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? null;
  return { blob: await res.blob(), filename };
}

export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    credentials: FETCH_CREDENTIALS,
    headers: await authHeaders(),
    body: form,
  });
  if (!res.ok) return parseError(res);
  return res.json() as Promise<T>;
}
