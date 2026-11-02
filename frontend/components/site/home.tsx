"use client";
import Link from "next/link";
import { motion, useInView, useMotionValueEvent, useReducedMotion, useScroll } from "motion/react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Wordmark } from "@/components/brand";
import { DUR, EASE, Reveal } from "@/components/motion";
import { api } from "@/lib/api";
import { Cite, Headlines } from "@/components/site/headlines";
import { DeployLauncher } from "@/components/deploy/launcher";
import { HeroCarousel, type Highlights } from "@/components/site/hero";

type Config = { tenant_name: string; highlights: { prudence?: Highlights; as_of: string } | null };


function useHighlights() {
  const [cfg, setCfg] = useState<Config | null>(null);
  useEffect(() => { api<Config>("/config").then(setCfg).catch(() => setCfg(null)); }, []);
  return { h: cfg?.highlights?.prudence ?? null, asOf: cfg?.highlights?.as_of ?? null, tenant: cfg?.tenant_name ?? "Meridian Energy" };
}

// ------------------------------------------------------------------------------------------------ nav
function Nav({ cta }: { cta: ReactNode }) {
  const { scrollY } = useScroll();
  const [dark, setDark] = useState(false); // the hero is light; only the security section is dark
  const [solid, setSolid] = useState(false);
  // The bar takes the theme of whichever section sits under its middle (sections opt in with data-nav="dark"). A section
  // that fades in at the top (CSS --fade-top) or out at the bottom (--fade) only counts as dark past the middle of the fade.
  useMotionValueEvent(scrollY, "change", (y) => {
    const under = [...document.querySelectorAll<HTMLElement>("[data-nav=dark]")].some((el) => {
      const r = el.getBoundingClientRect();
      const css = getComputedStyle(el);
      const fade = parseFloat(css.getPropertyValue("--fade")) || 0;
      const fadeTop = parseFloat(css.getPropertyValue("--fade-top")) || 0;
      return r.top + fadeTop / 2 <= 28 && r.bottom - fade / 2 >= 28;
    });
    setDark(under);
    setSolid(y > 8);
  });
  return (
    <motion.header className={`s-nav ${dark ? "on-dark" : "on-light"} ${solid ? "solid" : ""}`}
      initial={{ y: -24, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={{ duration: DUR.standard, ease: EASE }}>
      <div className="s-nav-in">
        <Wordmark size={22} />
        <nav className="s-links">
          <a href="#headlines">Why now</a>
          <a href="#platform">Platform</a>
          <a href="#how">How it works</a>
          <a href="#trust">Security</a>
          <a href="#deployment">Deployment</a>
        </nav>
        {cta}
      </div>
    </motion.header>
  );
}

const Chevron = () => (
  <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden><path d="M3 1.5 6.5 5 3 8.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
);

// ------------------------------------------------------------------------------------------------ how it works
const STEPS = [
  { t: "Connect", d: "FOCUS billing, SaaS invoices, provider recommendations and LLM gateway logs arrive as file drops. Nothing to install on your systems." },
  { t: "Allocate", d: <>Costs flow from the service company to each OpCo, jurisdiction and FERC account, under approved, effective-dated
    policies that produce a Form 60-style schedule.<Cite n={[11, 4]} /></> },
  { t: "Test", d: <>Every base-period line is checked for reconciliation, policy, FERC mapping under Order 898&apos;s computer
    accounts,<Cite n={8} /> and used-and-useful.</> },
  { t: "Seal", d: "Exhibits and workpapers export as one evidence pack, with its SHA-256 written to a hash-chained ledger." },
];

function How() {
  const ref = useRef<HTMLDivElement>(null);
  const seen = useInView(ref, { once: true, margin: "0px 0px -20% 0px" });
  return (
    <section id="how" className="s-how">
      <div className="s-wrap">
        <Reveal><div className="s-kicker">How it works</div></Reveal>
        <Reveal delay={0.06}><h2 className="s-h2">From invoice to exhibit.<br /><span className="dim">Without a spreadsheet in between.</span></h2></Reveal>
        <div ref={ref} className="s-steps">
          <svg className="s-steps-line" viewBox="0 0 1000 4" preserveAspectRatio="none" aria-hidden>
            <line x1="0" y1="2" x2="1000" y2="2" className="track" />
            <motion.line x1="0" y1="2" x2="1000" y2="2" className="fill" initial={{ pathLength: 0 }}
              animate={{ pathLength: seen ? 1 : 0 }} transition={{ duration: 1.6, ease: EASE, delay: 0.1 }} />
          </svg>
          {STEPS.map((s, k) => (
            <motion.div key={s.t} className="s-step" initial={{ opacity: 0, y: 24 }} animate={seen ? { opacity: 1, y: 0 } : {}}
              transition={{ duration: DUR.standard + 0.1, ease: EASE, delay: 0.15 + k * 0.32 }}>
              <motion.span className="dot" initial={{ scale: 0.4 }} animate={seen ? { scale: 1 } : {}}
                transition={{ duration: DUR.standard, ease: EASE, delay: 0.15 + k * 0.32 }}>{k + 1}</motion.span>
              <h3>{s.t}</h3>
              <p>{s.d}</p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ------------------------------------------------------------------------------------------------ platform bento
function Bars() {
  const ref = useRef<HTMLDivElement>(null);
  const seen = useInView(ref, { once: true, margin: "0px 0px -15% 0px" });
  const hs = [38, 52, 44, 61, 47, 58, 92, 88, 55, 49, 63, 57, 51, 66, 60, 54];
  return (
    <div ref={ref} className="b-bars" aria-hidden>
      {hs.map((v, k) => (
        <motion.i key={k} className={v > 80 ? "spike" : ""} initial={{ scaleY: 0 }} animate={seen ? { scaleY: v / 100 } : {}}
          transition={{ duration: DUR.slow, ease: EASE, delay: k * 0.03 }} />
      ))}
    </div>
  );
}

const STAGES = ["Identified", "Approved", "Implemented", "Verified"];
function Lifecycle() {
  const [step, setStep] = useState(0);
  const reduce = useReducedMotion();
  useEffect(() => {
    if (reduce) return;
    const t = setInterval(() => setStep((n) => (n + 1) % STAGES.length), 1500);
    return () => clearInterval(t);
  }, [reduce]);
  const k = reduce ? STAGES.length - 1 : step;
  return (
    <div className="b-life" aria-hidden>
      {STAGES.map((s, n) => (
        <span key={s} className={n === k ? "on" : n < k ? "done" : ""}>
          {n === k && <motion.i layoutId="life" transition={{ duration: DUR.standard, ease: EASE }} />}
          <em>{s}</em>
        </span>
      ))}
    </div>
  );
}

const HASH = "1353b112f08bfe297ed1c61b8a9b6d3daeb7b24f74a38d013d6d83b1a485f2f9";
function Decode() {
  const ref = useRef<HTMLDivElement>(null);
  const seen = useInView(ref, { once: true, margin: "0px 0px -15% 0px" });
  const reduce = useReducedMotion();
  const [step, setStep] = useState(0);
  useEffect(() => {
    if (!seen || reduce) return;
    let f = 0;
    const t = setInterval(() => { f += 2; setStep(Math.min(HASH.length, f)); if (f >= HASH.length) clearInterval(t); }, 28);
    return () => clearInterval(t);
  }, [seen, reduce]);
  const n = reduce && seen ? HASH.length : step;
  const hex = "0123456789abcdef";
  const text = HASH.split("").map((c, k) => (k < n ? c : seen ? hex[(k * 7 + n) % 16] : "·")).join("");
  return <div ref={ref} className="b-hash" aria-hidden><span className="lock">SHA-256</span><code>{text}</code></div>;
}

function Request() {
  const ref = useRef<HTMLDivElement>(null);
  const seen = useInView(ref, { once: true, margin: "0px 0px -15% 0px" });
  return (
    <div ref={ref} className="b-req" aria-hidden>
      <motion.div className="q" initial={{ opacity: 0, y: 12 }} animate={seen ? { opacity: 1, y: 0 } : {}} transition={{ duration: DUR.standard, ease: EASE }}>
        Provide cloud subscription costs for the test year, by vendor.
      </motion.div>
      <motion.div className="a" initial={{ opacity: 0, y: 12, scale: 0.98 }} animate={seen ? { opacity: 1, y: 0, scale: 1 } : {}}
        transition={{ duration: DUR.standard, ease: EASE, delay: 0.7 }}>
        <svg width="16" height="16" viewBox="0 0 16 16"><motion.path d="M3.5 8.5 6.5 11.5 12.5 4.5" fill="none" stroke="currentColor" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round" initial={{ pathLength: 0 }} animate={seen ? { pathLength: 1 } : {}}
          transition={{ duration: DUR.standard, ease: EASE, delay: 1.0 }} /></svg>
        Drafted from the test-year bridge · awaiting approval
      </motion.div>
    </div>
  );
}

function Wall() {
  return (
    <div className="b-wall" aria-hidden>
      {["Meridian Electric Ohio", "Meridian Electric Texas", "Meridian Transmission", "Meridian Renewables"].map((e, k) => (
        <span key={e} className={k === 0 ? "me" : ""}>{e}</span>
      ))}
    </div>
  );
}

function Platform() {
  return (
    <section id="platform" className="s-platform">
      <div className="s-wrap">
        <Reveal><div className="s-kicker">Platform</div></Reveal>
        <Reveal delay={0.06}><h2 className="s-h2">Built for the way utilities<br />are regulated.</h2></Reveal>
        <div className="s-bento">
          <Reveal className="tile wide">
            <h3>Cloud and AI, monitored daily.</h3>
            <p>Spend by provider, model and application, with anomalies, idle GPUs, unused provisioned capacity and cost per accepted outcome.</p>
            <Bars />
          </Reveal>
          <Reveal className="tile" delay={0.08}>
            <h3>Savings you can prove.</h3>
            <p>Every optimization moves from identified to verified, with a different person signing each step.</p>
            <Lifecycle />
          </Reveal>
          <Reveal className="tile">
            <h3>Evidence that can&apos;t be edited.</h3>
            <p>One-click rate-case packs. Anyone can check a filed exhibit against the ledger.</p>
            <Decode />
          </Reveal>
          <Reveal className="tile" delay={0.08}>
            <h3>Data requests, answered.</h3>
            <p>Staff and intervenor questions drafted from tested queries. No LLM, nothing leaves.</p>
            <Request />
          </Reveal>
          <Reveal className="tile" delay={0.16}>
            <h3>An affiliate wall that holds.</h3>
            <p>Each OpCo sees only its own allocated cost. Never the shared pool, never a sibling.</p>
            <Wall />
          </Reveal>
        </div>
      </div>
    </section>
  );
}

// ------------------------------------------------------------------------------------------------ trust + close
const TRUST = [
  { t: "On-prem or your VPC.", d: "CostIQ runs where your data already lives. Billing exports, invoices and telemetry never leave your boundary." },
  { t: "No LLM in the loop.", d: "Answers come from 145 tested, deterministic queries. Every figure shows the SQL and sources behind it." },
  { t: "NERC CIP aware.", d: <>BCSI in cloud services,<Cite n={12} /> CIP-013 vendor clauses<Cite n={[9, 14]} /> and grid-ops AI reviews are
    checked alongside cost.</> },
];

function Trust() {
  return (
    <section id="trust" className="s-trust" data-nav="dark">
      <div className="s-wrap">
        <Reveal><h2 className="s-h2 light">Runs inside<br /><span className="grad">your boundary.</span></h2></Reveal>
        <div className="s-trust-grid">
          {TRUST.map((x, k) => (
            <Reveal key={x.t} delay={0.08 * k}><h3>{x.t}</h3><p>{x.d}</p></Reveal>
          ))}
        </div>
        <Reveal delay={0.1} className="s-trust-cta" id="deployment">
          <div>
            <h3>See exactly what it takes to run in your environment.</h3>
            <p>Answer a guided set of questions and get a full blueprint: hardware, software, firewall and F5 configuration,
              cyber defense, compliance, quality gates, the release route and the development plan. Anything you already run, we plug into.</p>
          </div>
          <DeployLauncher />
        </Reveal>
      </div>
    </section>
  );
}

export function Home({ navCta }: { navCta: ReactNode }) {
  const { h } = useHighlights();
  return (
    <div className="site">
      <Nav cta={navCta} />
      <main>
        <HeroCarousel h={h} />
        <Headlines />
        <Platform />
        <How />
        <Trust />
      </main>
    </div>
  );
}

export function CtaLink({ href, children, variant = "primary" }: { href: string; children: ReactNode; variant?: "primary" | "compact" }) {
  return (
    <Link href={href} className={`s-btn ${variant}`}>
      <span>{children}</span>
      {variant !== "compact" && <Chevron />}
    </Link>
  );
}
