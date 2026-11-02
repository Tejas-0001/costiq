"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { signOut } from "@/app/actions";
import { Wordmark } from "@/components/brand";
import { Agent } from "@/components/agent";
import { NavIcon } from "@/components/icons";
import { EASE } from "@/components/motion";
import { api } from "@/lib/api";

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

// small per-browser preferences (sidebar, period, theme); storage can be unavailable, so every access is guarded
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

export const NAV: { group: string; items: [string, string, string, boolean?][] }[] = [
  { group: "Overview", items: [["command", "/command", "Command Center"], ["explorer", "/explorer", "Spend Explorer"]] },
  { group: "Monitor", items: [["cloud", "/cloud", "Cloud Cost Monitor", true], ["ai", "/ai", "AI & LLM Cost Monitor", true], ["*", "/alerts", "Alerts"]] },
  { group: "Optimize", items: [["optimization", "/optimization", "Optimization Center", true], ["anomalies", "/anomalies", "Anomalies"], ["rates", "/rates", "Rates & Commitments"]] },
  { group: "Financial management", items: [["budgets", "/budgets", "Budgets & Forecasts"], ["allocation", "/allocation", "Allocation & Chargeback"]] },
  { group: "Regulatory & compliance", items: [["prudence", "/prudence", "Prudence Radar", true], ["regulatory", "/regulatory", "Regulatory Accounting", true],
    ["utilities", "/utilities", "Utility Compliance", true], ["reconciliation", "/reconciliation", "Reconciliation & Close"], ["governance", "/governance", "Governance & Evidence"]] },
  { group: "Intelligence", items: [["catalog", "/catalog", "Use Case Catalog"], ["ask", "/ask", "Ask CostIQ"], ["*", "/deploy?from=app", "Deployment planner", true]] },
];

/** First page a persona can open (OpCo finance has no Command Center). */
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
  const [collapsed, setCollapsed] = useState(() => pref<string>("costiq-nav", "open") === "collapsed");
  const [period, setPeriodState] = useState<Period>(() => pref<Period>("costiq-period", "mtd"));
  const [theme, setTheme] = useState<string>(() => pref<string>("costiq-theme", "light"));
  const setPeriod = useCallback((v: Period) => { setPeriodState(v); savePref("costiq-period", v); }, []);
  const toggleNav = () => { const v = !collapsed; setCollapsed(v); savePref("costiq-nav", v ? "collapsed" : "open"); };
  const toggleTheme = () => {
    const v = theme === "dark" ? "light" : "dark";
    setTheme(v); savePref("costiq-theme", v); document.documentElement.dataset.theme = v;
  };

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
      <div className={`shell ${collapsed ? "collapsed" : ""}`}>
        <nav className="nav" aria-label="CostIQ">
          <div className="brand">
            <div className="brand-mark"><Wordmark href="/command" size={26} /><small>{tenant}</small></div>
            <button type="button" className="nav-toggle" onClick={toggleNav} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} title={collapsed ? "Expand" : "Collapse"}>
              <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden><rect x="3.5" y="4.5" width="17" height="15" rx="3" fill="none" stroke="currentColor" strokeWidth="1.8" />
                <path d="M9.5 4.5v15" stroke="currentColor" strokeWidth="1.8" /></svg>
            </button>
          </div>
          {NAV.map((g) => {
            const items = g.items.filter(([m]) => can(m));
            if (!items.length) return null;
            return (
              <div key={g.group}>
                <div className="group">{g.group}</div>
                {items.map(([, href, labelText, isNew]) => {
                  const on = path.startsWith(href.split("?")[0]);
                  const icon = href.split("?")[0].slice(1) || "command";
                  return (
                    <Link key={href} href={href} className={on ? "active" : ""} aria-current={on ? "page" : undefined} title={collapsed ? labelText : undefined}>
                      {on && <motion.span layoutId="nav-hl" className="nav-hl" transition={{ duration: 0.45, ease: EASE }} />}
                      <NavIcon name={icon} /><span className="nav-label">{labelText}</span>{isNew && <span className="new">New</span>}
                    </Link>
                  );
                })}
              </div>
            );
          })}
        </nav>
        <div className="main">
          <div className="topbar">
            <div className="period" role="radiogroup" aria-label="Period">
              {PERIODS.map((p) => (
                <button key={p.key} type="button" role="radio" aria-checked={period === p.key} className={period === p.key ? "on" : ""} onClick={() => setPeriod(p.key)}>
                  {period === p.key && <motion.span layoutId="period-hl" className="period-hl" transition={{ duration: 0.35, ease: EASE }} />}
                  <span>{p.label}</span>
                </button>
              ))}
            </div>
            <span className="asof" title="Latest billed day in the data">as of {me.as_of}</span>
            <span className="spacer" />
            <span className="pill scope" title="Your data scope">{me.scope === "enterprise" ? "Enterprise" : (me.scope as string[]).join(", ") || "none"}</span>
            <span className="pill demo" title="Synthetic data for a fictional utility">Demo</span>
            <button type="button" className="bell" onClick={toggleTheme} aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"} title={theme === "dark" ? "Light mode" : "Dark mode"}>
              <AnimatePresence mode="wait" initial={false}>
                <motion.svg key={theme} width="17" height="17" viewBox="0 0 24 24" aria-hidden initial={{ rotate: -90, scale: 0.5, opacity: 0 }}
                  animate={{ rotate: 0, scale: 1, opacity: 1 }} exit={{ rotate: 90, scale: 0.5, opacity: 0 }} transition={{ duration: 0.3, ease: EASE }}>
                  {theme === "dark"
                    ? <path d="M12 4V2m0 20v-2m8-8h2M2 12h2m13.7-5.7 1.4-1.4M4.9 19.1l1.4-1.4m0-11.4L4.9 4.9m14.2 14.2-1.4-1.4M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                    : <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />}
                </motion.svg>
              </AnimatePresence>
            </button>
            <Link href="/alerts" className="bell" title={`Alerts${alerts ? ` (${alerts} new)` : ""}`} aria-label="Alerts">
              <svg width="17" height="17" viewBox="0 0 24 24" aria-hidden><path d="M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15L6 16Zm4 4a2 2 0 0 0 4 0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
              <AnimatePresence>{alerts > 0 && (
                <motion.span key="c" className="count" initial={{ scale: 0 }} animate={{ scale: 1 }} exit={{ scale: 0 }} transition={{ duration: 0.35, ease: EASE }}>{alerts}</motion.span>
              )}</AnimatePresence>
            </Link>
            <div className="who">
              <span className="avatar" aria-hidden>{persona.replace(/\s*\(.*\)$/, "").split(/[\s&/-]+/).filter((w) => /^[A-Z]/.test(w)).slice(0, 2).map((w) => w[0]).join("")}</span>
              <div className="who-text"><div className="name">{persona}</div><div className="mail">{me.user}</div></div>
            </div>
            <form action={signOut}><button className="btn sm">Switch role</button></form>
          </div>
          <motion.div key={path} className="content" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45, ease: EASE }}>
            {children}
          </motion.div>
        </div>
      </div>
      <Agent />
    </MeContext.Provider>
  );
}

/** Rendered when the persona's role does not include a module (the API refuses it too). */
export function Gate({ module, children }: { module: string; children: ReactNode }) {
  const { me } = useMe();
  if (!me.modules.includes(module)) {
    return (
      <div className="card">
        <h2>Not in your role</h2>
        <p className="muted">The {module} module is not available to {me.personas[0]?.label}. <Link href={homeFor(me.modules)}>Go to your home page</Link>.</p>
      </div>
    );
  }
  return <>{children}</>;
}
