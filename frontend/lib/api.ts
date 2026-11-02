"use client";
// Browser-side API client. Every call goes to this app's /api route handler, which forwards it to the FastAPI backend
// with the demo persona from the session cookie (the browser never sends the persona header itself).
import { useEffect, useState } from "react";

export type Row = Record<string, unknown>;
export type Result = { columns: string[]; rows: unknown[][]; elapsed_ms?: number; params?: Record<string, unknown> };

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function handle<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* not JSON */
    }
    throw new ApiError(detail, r.status);
  }
  return r.json() as Promise<T>;
}

export function api<T = Row>(path: string): Promise<T> {
  return fetch(`/api${path}`, { cache: "no-store" }).then((r) => handle<T>(r));
}

export function post<T = Row>(path: string, body?: unknown): Promise<T> {
  return fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  }).then((r) => handle<T>(r));
}

export function upload<T = Row>(path: string, file: File): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  return fetch(`/api${path}`, { method: "POST", body: fd }).then((r) => handle<T>(r));
}

/** Download a file from the API; returns the response headers (e.g. the evidence pack's SHA-256). */
export async function download(path: string, filename: string): Promise<Headers> {
  const r = await fetch(`/api${path}`);
  if (!r.ok) {
    await handle(r);
  }
  const blob = await r.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
  return r.headers;
}

export type ApiState<T> = { data: T | null; error: Error | null; loading: boolean };

/** Fetch `path` and refetch when it (or `version`) changes. Stale responses are ignored. */
export function useApi<T = Row>(path: string | null, version: number = 0): ApiState<T> {
  const [state, set] = useState<ApiState<T>>({ data: null, error: null, loading: !!path });
  useEffect(() => {
    if (!path) return;
    let live = true;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- show the loading state for a new request
    set((s) => ({ ...s, loading: true }));
    api<T>(path)
      .then((data) => live && set({ data, error: null, loading: false }))
      .catch((error: Error) => live && set({ data: null, error, loading: false }));
    return () => {
      live = false;
    };
  }, [path, version]);
  return state;
}

/** Result (columns + rows) as records. */
export function records<T = Row>(res: Result | null | undefined): T[] {
  if (!res) return [];
  return res.rows.map((r) => Object.fromEntries(res.columns.map((c, i) => [c, r[i]])) as T);
}
