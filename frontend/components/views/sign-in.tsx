"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useState, useTransition } from "react";
import { choosePersona } from "@/app/actions";
import { Wordmark } from "@/components/brand";
import { CountUp, DUR, EASE, GridNetwork } from "@/components/motion";
import { api } from "@/lib/api";
import { compact } from "@/lib/format";

type Persona = { key: string; label: string; group: string };
type Config = {
  tenant_name: string; personas: Persona[];
  highlights: { prudence?: { disallowance_risk: number; earnings_unlock: number; verified_savings_annual: number }; as_of: string } | null;
};

const TABS = [
  { group: "Finance Leadership & Planning", tab: "Finance", tone: "fin" },
  { group: "Regulatory & Compliance", tab: "Regulatory", tone: "reg" },
  { group: "Technology & Operations", tab: "Technology", tone: "tech" },
  { group: "Governance", tab: "Governance", tone: "gov" },
];
const WHO: Record<string, string> = {
  cfo: "Disallowance risk, earnings unlock and verified savings",
  pl_exec: "Your business unit's spend and the findings to endorse",
  fpa_partner: "Forecasts and budget position for your cost centers",
  fpa_analyst: "Enterprise forecasts, budgets and commitments",
  finance_controller: "Close, invoices and allocation policies to approve",
  regulatory_analyst: "Rate-case bridge, evidence packs and data requests",
  affiliate_accounting: "Service-company allocation drivers and Form 60",
  cip_compliance: "BCSI in the cloud and CIP-013 vendor clauses",
  opco_finance: "Meridian Electric Ohio only, behind the affiliate wall",
  tech_exec: "Cloud and AI spend, optimization approvals",
  finops_analyst: "Monitors, anomalies and verifying savings",
  cloud_ops: "Implementing optimizations in your cost centers",
  ai_program_owner: "LLM unit cost, PTU and GPU waste, routing",
  auditor: "Controls, the ledger and evidence",
};

const title = (label: string) => label.replace(/\s*\(.*\)\s*$/, "");
const initials = (label: string) => title(label).split(/[\s&/-]+/).filter((w) => /^[A-Za-z]/.test(w) && w !== "and").slice(0, 2).map((w) => w[0].toUpperCase()).join("");

const rise = (d: number) => ({
  initial: { opacity: 0, y: 18, filter: "blur(6px)" },
  animate: { opacity: 1, y: 0, filter: "blur(0px)" },
  transition: { duration: DUR.standard + 0.15, ease: EASE, delay: d },
});

/** `signedIn` comes from the server (the session cookie is http-only), so a signed-out visitor never sees "you're signed in as". */
export function SignIn({ signedIn = false }: { signedIn?: boolean }) {
  const [cfg, setCfg] = useState<Config | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tabPick, setTab] = useState<string | null>(null);
  const [sel, setSel] = useState("cfo");
  const [pending, start] = useTransition();
  const next = useSearchParams().get("next") ?? "/command";

  const [current, setCurrent] = useState<{ key: string; label: string } | null>(null);
  const fetchConfig = () => api<Config>("/config").then(setCfg).catch((e: Error) => setErr(e.message));
  const load = () => { setErr(null); fetchConfig(); };
  useEffect(() => { fetchConfig(); }, []);
  // already signed in? start on the current role
  useEffect(() => {
    if (!signedIn) return;
    api<{ personas: { key: string; label: string }[] }>("/me")
      .then((me) => { const p = me.personas[0]; if (p) { setCurrent(p); setSel(p.key); } })
      .catch(() => {});
  }, [signedIn]);
  // until a tab is picked, show the one that holds the selected role (the current role when already signed in)
  const tab = tabPick ?? cfg?.personas.find((p) => p.key === sel)?.group ?? TABS[0].group;

  const roles = useMemo(() => (cfg?.personas ?? []).filter((p) => p.group === tab), [cfg, tab]);
  const chosen = cfg?.personas.find((p) => p.key === sel);
  const tone = (group: string) => TABS.find((t) => t.group === group)?.tone ?? "fin";
  const h = cfg?.highlights?.prudence;

  return (
    <div className="auth">
      <aside className="auth-art">
        <div className="aurora" />
        <GridNetwork />
        <motion.div className="auth-art-top" {...rise(0.1)}><Wordmark tone="light" size={24} /></motion.div>
        <div className="auth-art-copy">
          <motion.h2 {...rise(0.25)}>Every number on screen<br />comes with its <span className="grad">evidence.</span></motion.h2>
          <motion.dl {...rise(0.4)}>
            <div><dt>Disallowance risk</dt><dd className="risk"><CountUp value={h?.disallowance_risk} format={compact} /></dd></div>
            <div><dt>Earnings unlock</dt><dd className="unlock"><CountUp value={h?.earnings_unlock} format={(n) => `${compact(n)}/yr`} /></dd></div>
            <div><dt>Verified savings</dt><dd className="save"><CountUp value={h?.verified_savings_annual} format={(n) => `${compact(n)}/yr`} /></dd></div>
          </motion.dl>
        </div>
        <motion.p className="auth-art-foot" {...rise(0.55)}>{cfg?.tenant_name ?? "Meridian Energy"} is a fictional utility. All demo data is synthetic.</motion.p>
      </aside>

      <main className="auth-main">
        <motion.div className="auth-bar" {...rise(0)}>
          <span className="auth-mobile-mark"><Wordmark tone="dark" size={22} /></span>
          <Link href="/" className="auth-back"><svg width="10" height="10" viewBox="0 0 10 10" aria-hidden><path d="M6.5 1.5 3 5l3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>Home</Link>
        </motion.div>

        <form className="auth-card" onSubmit={(e) => { e.preventDefault(); if (chosen) start(() => choosePersona(chosen.key, next)); }}>
          <motion.h1 {...rise(0.08)}>{current ? "Choose a role" : "Sign in to CostIQ"}</motion.h1>
          <motion.p className="auth-sub" {...rise(0.14)}>
            {current
              ? <>You&apos;re signed in as {current.label.replace(/\s*\(.*\)\s*$/, "")}. Continue as that role, or switch to another.</>
              : <>Choose a role to explore the {cfg?.tenant_name ?? "Meridian Energy"} demo. Each role sees its own modules, scope and approvals.</>}
          </motion.p>

          <motion.div className="seg" role="tablist" aria-label="Role group" {...rise(0.2)}>
            {TABS.map((t) => (
              <button key={t.group} type="button" role="tab" aria-selected={tab === t.group} className={tab === t.group ? "on" : ""}
                onClick={() => { setTab(t.group); const first = cfg?.personas.find((p) => p.group === t.group); if (first) setSel(first.key); }}>
                {tab === t.group && <motion.span layoutId="seg" className="seg-pill" transition={{ duration: 0.45, ease: EASE }} />}
                <span className="seg-label">{t.tab}</span>
              </button>
            ))}
          </motion.div>

          <motion.div className="roles" role="radiogroup" aria-label="Role" {...rise(0.26)}>
            {err ? (
              <div className="auth-err">
                <b>CostIQ can&apos;t reach its API.</b><span>{err}</span>
                <button type="button" className="auth-retry" onClick={load}>Try again</button>
              </div>
            ) : !cfg ? (
              Array.from({ length: 4 }, (_, k) => <div key={k} className="role skeleton" style={{ animationDelay: `${k * 0.08}s` }} />)
            ) : (
              <AnimatePresence mode="popLayout" initial={false}>
                {roles.map((p, k) => (
                  <motion.label key={p.key} className={`role ${sel === p.key ? "on" : ""}`} layout="position"
                    initial={{ opacity: 0, y: 10, filter: "blur(4px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                    exit={{ opacity: 0, transition: { duration: DUR.quick } }}
                    transition={{ duration: DUR.standard, ease: EASE, delay: k * 0.035 }}>
                    <input type="radio" name="persona" value={p.key} checked={sel === p.key} onChange={() => setSel(p.key)} />
                    {sel === p.key && <motion.span layoutId="role-hl" className="role-hl" transition={{ duration: 0.45, ease: EASE }} />}
                    <span className={`avatar ${tone(p.group)}`}>{initials(p.label)}</span>
                    <span className="role-text"><b>{title(p.label)}</b><small>{WHO[p.key] ?? ""}</small></span>
                    <span className="role-check" aria-hidden>
                      <svg width="12" height="12" viewBox="0 0 12 12"><path d="M2.5 6.3 5 8.7l4.5-5.2" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" /></svg>
                    </span>
                  </motion.label>
                ))}
              </AnimatePresence>
            )}
          </motion.div>

          <motion.div {...rise(0.32)}>
            <motion.button type="submit" className="auth-go" disabled={!chosen || pending} whileTap={{ scale: 0.98 }}>
              <AnimatePresence mode="wait" initial={false}>
                <motion.span key={pending ? "p" : chosen?.key ?? "none"} className="auth-go-in"
                  initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.22, ease: EASE }}>
                  {pending ? <><span className="spin" />Signing in…</> : <>Continue as {chosen ? title(chosen.label) : "…"}<svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
                    <path d="M2 6h7.5M6.5 2.5 10 6l-3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg></>}
                </motion.span>
              </AnimatePresence>
            </motion.button>
            <p className="auth-note">Demo sign-in, no password. For production, CostIQ supports Microsoft Entra ID.</p>
          </motion.div>
        </form>
      </main>
    </div>
  );
}
