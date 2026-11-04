"use client";
// Tenant copy: the words that differ between tenant packs (backend/tenants/<tenant>/copy.json merged with the fragments in
// copy/*.json, "frontend" section). The API serves it with /api/me (inside the app shell) and /api/config (landing and other
// public pages; see PublicCopy below).
//
//   const { t, get, tenant } = useCopy();
//   t("nav.items.risk")                      -> the risk page's name in this tenant
//   t("tax.tabs.credit", { year: 2026 })     -> fills {year} placeholders
//   get<string[]>("glossary.terms")          -> any JSON value (lists, objects) for tables and term lists
//   copyText("format.moneyColumns")          -> outside React (lib/format.ts), once rememberCopy() has run
//
// Wiring: whoever fetches /api/me or /api/config calls rememberCopy(r.tenant, r.copy) in its .then() and renders
// <CopyProvider tenant={r.tenant} copy={r.copy}> around the page (the app shell does both).
//
// Keys are dotted paths. Every tenant's copy.json has the same keys (backend test test_tenants.py), so a page reads one
// key for every tenant; never fall back to another tenant's words in code.
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "@/lib/api";

export type CopyValue = string | number | boolean | null | CopyValue[] | { [key: string]: CopyValue };
export type Copy = { [key: string]: CopyValue };

type CopyCtx = { tenant: string; copy: Copy };
const CopyContext = createContext<CopyCtx>({ tenant: "", copy: {} });
let current: CopyCtx = { tenant: "", copy: {} }; // for helpers that run outside React (see rememberCopy)

/** The value at a dotted key, or undefined. */
export function lookup<T = CopyValue>(copy: Copy, key: string): T | undefined {
  let node: CopyValue | undefined = copy;
  for (const part of key.split(".")) {
    if (node === null || typeof node !== "object" || Array.isArray(node)) return undefined;
    node = (node as { [key: string]: CopyValue })[part];
  }
  return node as T | undefined;
}

/** Fill {name} placeholders. */
export function fill(text: string, vars?: Record<string, string | number>): string {
  return vars ? text.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k]) : m)) : text;
}

function textOf(copy: Copy, key: string, vars?: Record<string, string | number>, fallback = ""): string {
  const v = lookup(copy, key);
  return typeof v === "string" ? fill(v, vars) : typeof v === "number" ? String(v) : fallback;
}

/** Keep the copy from an API response for helpers outside React (call it in the fetch callback, not during render). */
export function rememberCopy(tenant: string | undefined, copy: Copy | undefined) {
  current = { tenant: tenant ?? "", copy: copy ?? {} };
}

/** Copy text outside React components (the copy passed to rememberCopy). */
export function copyText(key: string, vars?: Record<string, string | number>, fallback = ""): string {
  return textOf(current.copy, key, vars, fallback);
}

/** Copy value of any shape outside React components. */
export function copyValue<T = CopyValue>(key: string): T | undefined {
  return lookup<T>(current.copy, key);
}

export function CopyProvider({ tenant, copy, children }: { tenant?: string; copy?: Copy; children: ReactNode }) {
  const value = useMemo(() => ({ tenant: tenant ?? "", copy: copy ?? {} }), [tenant, copy]);
  return <CopyContext.Provider value={value}>{children}</CopyContext.Provider>;
}

type PublicConfig = { tenant?: string; copy?: Copy };

/** Copy for a public page outside the app shell (glossary): fetches /api/config once, then renders its children with
 *  the tenant's copy. */
export function PublicCopy({ children }: { children: ReactNode }) {
  const [cfg, setCfg] = useState<PublicConfig | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api<PublicConfig>("/config").then((c) => { rememberCopy(c.tenant, c.copy); setCfg(c); }).catch((e: Error) => setErr(e.message));
  }, []);
  if (err) return <div className="error" style={{ margin: "12px 0" }}>CostIQ can&apos;t reach its API: {err}</div>;
  if (!cfg) return <div className="muted small">Loading…</div>;
  return <CopyProvider tenant={cfg.tenant} copy={cfg.copy}>{children}</CopyProvider>;
}

export function useCopy() {
  const { tenant, copy } = useContext(CopyContext);
  return useMemo(() => ({
    tenant,
    copy,
    /** Text at a dotted key with {placeholders} filled; `fallback` (default "") when the key is missing. */
    t: (key: string, vars?: Record<string, string | number>, fallback = "") => textOf(copy, key, vars, fallback),
    /** Any JSON value at a dotted key. */
    get: <T = CopyValue,>(key: string): T | undefined => lookup<T>(copy, key),
  }), [tenant, copy]);
}
