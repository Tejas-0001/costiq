"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, Suspense, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { signOut } from "@/app/actions";
import { Agent } from "@/components/agent";
import { Arrival } from "@/components/arrival";
import { api } from "@/lib/api";
import { MMMark } from "@/components/brand";

export type Me = {
  user: string; name: string; personas: { key: string; label: string }[]; modules: string[]; actions: string[];
  scope: "enterprise" | string[]; pii: boolean; as_of: string;
};
export type Period = "today" | "yesterday" | "wtd" | "mtd" | "ytd";
export const PERIODS: { key: Period; label: string; long: string }[] = [
  { key: "today", label: "Today", long: "today" }, { key: "yesterday", label: "Yesterday", long: "yesterday" },
  { key: "wtd", label: "WTD", long: "week to date" }, { key: "mtd", label: "MTD", long: "month to date" }, { key: "ytd", label: "YTD", long: "year to date" },
];
type Ctx = { me: Me; tenant: string; version: number; refresh: () => void; period: Period; setPeriod: (p: Period) => void };
/** The window a page's figures cover, as the API reports it. */
export type PeriodInfo = { range: Period; name: string; long: string; start: string; end: string; label: string };

// Pages whose main figures belong to a fixed period; the selector still drives the use cases listed on them.
const FIXED: [string, string][] = [
  ["/board", "Board Pack: data to date"], ["/budgets", "Budgets: by month"], ["/optimization", "Savings pipeline: current"],
  ["/risk", "Tax risk: filing review periods"], ["/tax", "Tax: filing periods"], ["/compliance", "GxP checks: current"],
  ["/reconciliation", "Invoices: by billing period"], ["/governance", "Ledger: all decisions"], ["/anomalies", "Findings: all open"],
];

// small per-browser preferences (period); storage can be unavailable, so every access is guarded
function pref<T extends string>(key: string, fallback: T): T {
  try { return (typeof window !== "undefined" && (localStorage.getItem(key) as T)) || fallback; } catch { return fallback; }
}
function savePref(key: string, v: string) { try { localStorage.setItem(key, v); } catch { /* unavailable */ } }
const MeContext = createContext<Ctx | null>(null);

export function useMe(): Ctx {
  const c = useContext(MeContext);
  if (!c) throw new Error("useMe outside the app shell");
  return c;
}

export const NAV: { group: string; items: [string, string, string][] }[] = [
  { group: "Overview", items: [["*", "/summary", "AI Summary"], ["command", "/command", "Command Center"], ["board", "/board", "Board Pack"], ["explorer", "/explorer", "Spend Explorer"]] },
  { group: "Monitor", items: [["cloud", "/cloud", "Cloud Cost Monitor"], ["ai", "/ai", "AI & LLM Cost Monitor"], ["*", "/alerts", "Alerts"]] },
  { group: "Optimize", items: [["optimization", "/optimization", "Optimization Center"], ["anomalies", "/anomalies", "Anomalies"], ["rates", "/rates", "Rates & Commitments"]] },
  { group: "Financial management", items: [["budgets", "/budgets", "Budgets & Forecasts"], ["allocation", "/allocation", "Allocation & Chargeback"]] },
  { group: "Regulatory & compliance", items: [["risk", "/risk", "Tax Risk Radar"], ["tax", "/tax", "Tax & Transfer Pricing"],
    ["compliance", "/compliance", "GxP & Patient Data"], ["reconciliation", "/reconciliation", "Reconciliation & Close"], ["governance", "/governance", "Governance & Evidence"]] },
  { group: "Intelligence", items: [["catalog", "/catalog", "Use Case Catalog"], ["ask", "/ask", "Ask CostIQ"], ["*", "/glossary", "Glossary"], ["*", "/deploy?from=app", "Deployment planner"]] },
];

/** First page a persona can open (some roles have no Command Center). */
export function homeFor(modules: string[]): string {
  for (const g of NAV) for (const [m, href] of g.items) if (m !== "*" && modules.includes(m)) return href;
  return "/catalog";
}

export function Shell({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [tenant, setTenant] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [alerts, setAlerts] = useState(0);
  const path = usePathname();
  const refresh = useCallback(() => setVersion((v) => v + 1), []);
  const [period, setPeriodState] = useState<Period>(() => pref<Period>("costiq-period", "mtd"));
  const setPeriod = useCallback((v: Period) => { setPeriodState(v); savePref("costiq-period", v); }, []);

  useEffect(() => {
    api<Me>("/me").then(setMe).catch((e: Error) => setErr(e.message));
    api<{ tenant_name: string }>("/config").then((c) => setTenant(c.tenant_name)).catch(() => {});
  }, []);
  useEffect(() => {
    api<{ unacknowledged: number }>("/alerts").then((a) => setAlerts(a.unacknowledged)).catch(() => {});
  }, [version, path]);

  if (err) {
    return (
      <div className="content">
        <div className="error">{err}</div>
        <form action={signOut} style={{ marginTop: 12 }}><button className="btn">Back to sign-in</button></form>
      </div>
    );
  }
  if (!me) return <div className="content muted small">Loading…</div>;
  const can = (m: string) => m === "*" || me.modules.includes(m);
  const persona = me.personas[0]?.label ?? "";
  return (
    <MeContext.Provider value={{ me, tenant, version, refresh, period, setPeriod }}>
      <div className="shell">
        <nav className="nav" aria-label="CostIQ">
          <Link href={homeFor(me.modules)} className="brand"><MMMark size={34} /><span>Cost<em>IQ</em><small>MIRACLE MINDS</small></span></Link>
          {NAV.map((g) => {
            const items = g.items.filter(([m]) => can(m));
            if (!items.length) return null;
            return (
              <div key={g.group}>
                <div className="group">{g.group}</div>
                {items.map(([, href, labelText]) => {
                  const on = path.startsWith(href.split("?")[0]);
                  return <Link key={href} href={href} className={on ? "active" : ""} aria-current={on ? "page" : undefined}>{labelText}</Link>;
                })}
              </div>
            );
          })}
        </nav>
        <div className="main">
          <div className="topbar">
            <span className="pill">Scope <b>{me.scope === "enterprise" ? "Enterprise" : (me.scope as string[]).join(", ") || "none"}</b></span>
            <span className="pill">Data as of <b>{me.as_of}</b></span>
            <select value={period} onChange={(e) => setPeriod(e.target.value as Period)} aria-label="Period">
              {PERIODS.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
            </select>
            {(() => {
              const note = FIXED.find(([pre]) => path.startsWith(pre))?.[1];
              return note ? <span className="pnote" title="The period applies to the use cases on this page">{note}</span> : null;
            })()}
            <span className="spacer" />
            <Link href="/alerts" className={`pill ${alerts ? "alert" : ""}`}>Alerts <b>{alerts}</b></Link>
            <span className="who"><b>{persona}</b>{me.name && me.name !== persona && <><br /><span className="muted">{me.name}</span></>}</span>
            <form action={signOut}><button className="btn sm">Sign out</button></form>
          </div>
          <div className="content">{children}</div>
        </div>
      </div>
      <Agent />
      <Suspense fallback={null}><Arrival /></Suspense>
    </MeContext.Provider>
  );
}

/** Rendered when the persona's role does not include a module (the API refuses it too). */
export function Gate({ module, children }: { module: string; children: ReactNode }) {
  const { me } = useMe();
  if (!me.modules.includes(module)) {
    return (
      <p>Not available to {me.personas[0]?.label}. <Link href={homeFor(me.modules)}>Home</Link></p>
    );
  }
  return <>{children}</>;
}
