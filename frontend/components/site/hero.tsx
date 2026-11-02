"use client";
// Home hero: a scroll-driven carousel of the three Prudence Engine numbers. Each slide pairs the headline word with its
// number, the plain-English explanation (with sources) and a small live visual built from the demo data.
import Link from "next/link";
import { AnimatePresence, motion, useMotionValueEvent, useReducedMotion, useScroll, useTransform, type MotionValue } from "motion/react";
import { useCallback, useRef, useState, type ReactNode } from "react";
import { CountUp, EASE, EASE_IN } from "@/components/motion";
import { Cite } from "@/components/site/headlines";
import { compact } from "@/lib/format";

export type Highlights = {
  disallowance_risk: number; earnings_unlock: number; verified_savings_annual: number;
  by_reason?: { Label: string; Risk: number }[];
  jurisdictions?: { Jurisdiction: string; SteadyStateReturn: number; Earned: boolean; DeferralStatus: string }[];
  stages?: { Stage: string; MonthlySavings: number }[];
};

const usd = (n: number) => compact(n);
const perYear = (n: number) => `${compact(n)}/yr`;

type Slide = {
  key: "risk" | "unlock" | "save"; word: string; label: string; title: string; body: ReactNode; note: string;
  href: string; cta: string; pick: (h: Highlights) => number; format: (n: number) => string; Visual: (p: { h: Highlights | null }) => ReactNode;
};

// ------------------------------------------------------------------------------------------------ slide visuals
const SHORT: Record<string, string> = {
  "Known savings left unactioned for 60+ days": "Savings left unactioned",
  "Not used and useful (idle, unused seats or commitments)": "Not used and useful",
  "Not reconciled to an invoice": "Not reconciled to an invoice",
  "No FERC account": "No FERC account",
};

function RiskBars({ h }: { h: Highlights | null }) {
  const rows = h?.by_reason ?? [];
  const max = Math.max(1, ...rows.map((r) => r.Risk));
  return (
    <div className="hv-bars">
      <span className="hv-k">Why it&apos;s at risk</span>
      {rows.map((r, i) => (
        <div key={r.Label} className="row">
          <span className="lbl">{SHORT[r.Label] ?? r.Label}</span>
          <span className="track"><motion.i className="risk" initial={{ width: 0 }} animate={{ width: `${(r.Risk / max) * 100}%` }}
            transition={{ duration: 0.9, ease: EASE, delay: 0.25 + i * 0.08 }} /></span>
          <span className="amt">{compact(r.Risk)}</span>
        </div>
      ))}
    </div>
  );
}

function StateColumns({ h }: { h: Highlights | null }) {
  const js = [...(h?.jurisdictions ?? [])].sort((a, b) => b.SteadyStateReturn - a.SteadyStateReturn);
  const max = Math.max(1, ...js.map((j) => j.SteadyStateReturn));
  return (
    <div className="hv-cols">
      <span className="hv-k">Return a year, by state</span>
      <div className="cols">
        {js.map((j, i) => (
          <div key={j.Jurisdiction} className="col">
            <span className="amt">{compact(j.SteadyStateReturn)}</span>
            <span className="bar"><motion.i className={j.Earned ? "earned" : "whatif"} initial={{ height: 0 }} animate={{ height: `${(j.SteadyStateReturn / max) * 100}%` }}
              transition={{ duration: 0.9, ease: EASE, delay: 0.25 + i * 0.08 }} /></span>
            <span className="st">{j.Jurisdiction}</span>
          </div>
        ))}
      </div>
      <span className="legend"><i className="earned" />Earned under an order<i className="whatif" />If deferral were allowed</span>
    </div>
  );
}

function Funnel({ h }: { h: Highlights | null }) {
  const st = h?.stages ?? [];
  const max = Math.max(1, ...st.map((s) => s.MonthlySavings));
  return (
    <div className="hv-bars">
      <span className="hv-k">Savings a month, by stage</span>
      {st.map((s, i) => (
        <div key={s.Stage} className={`row ${s.Stage === "Verified" ? "hi" : ""}`}>
          <span className="lbl">{s.Stage}</span>
          <span className="track"><motion.i className={s.Stage === "Verified" ? "save" : "muted"} initial={{ width: 0 }}
            animate={{ width: `${Math.max(2, (s.MonthlySavings / max) * 100)}%` }} transition={{ duration: 0.9, ease: EASE, delay: 0.25 + i * 0.08 }} /></span>
          <span className="amt">{compact(s.MonthlySavings)}</span>
        </div>
      ))}
    </div>
  );
}

const SLIDES: Slide[] = [
  { key: "risk", word: "provable.", label: "Disallowance risk", title: "What a commission could challenge.", note: "Base-period spend a commission could challenge",
    href: "/prudence", cta: "Open the Prudence Radar", pick: (h) => h.disallowance_risk, format: usd, Visual: RiskBars,
    body: <>Idle GPU endpoints, unused seats, invoices that don&apos;t reconcile, spend allocated without an approved policy or a FERC account.
      Commission staff ask about it item by item.<Cite n={1} /> CostIQ lists every risky line by docket, OpCo and vendor, with the fix next to it.</> },
  { key: "unlock", word: "recoverable.", label: "Earnings unlock", title: "What cloud could earn.", note: "A year, if cloud fees were a regulatory asset",
    href: "/regulatory?tab=earnings", cta: "Model it state by state", pick: (h) => h.earnings_unlock, format: perYear, Visual: StateColumns,
    body: <>Cloud implementation costs can be capitalized, but subscription fees stay O&amp;M and earn no return.<Cite n={[13, 1]} /> Where a state
      allows deferral to a regulatory asset, as Idaho did, that gap closes.<Cite n={5} /> CostIQ models the return state by state, including the
      states that haven&apos;t decided yet.</> },
  { key: "save", word: "eliminated.", label: "Verified savings", title: "What you actually saved.", note: "Measured after each fix, carried into the rate case",
    href: "/optimization", cta: "Open the Optimization Center", pick: (h) => h.verified_savings_annual, format: perYear, Visual: Funnel,
    body: <>Measured from the run-rate after each fix, not estimated, and carried into the rate-case bridge as prudence evidence, the way SCE
      cited about $700K a year of SaaS consolidation savings.<Cite n={1} /> Whoever implements a change can&apos;t be the one who verifies it.</> },
];

// ------------------------------------------------------------------------------------------------ carousel
// Scroll-driven: the hero is pinned for one screen of scrolling per slide, so scrolling down walks through the three
// numbers and then the page carries on. Tabs, the arrow buttons, the arrow keys and a swipe all jump by scrolling to that
// slide's position, so the scroll position and the slide never disagree.
const N = SLIDES.length;

function Tab({ k, active, progress, h, onClick }: { k: number; active: boolean; progress: MotionValue<number>; h: Highlights | null; onClick: () => void }) {
  const x = SLIDES[k];
  // this tab's bar fills while its slide is on screen
  const fill = useTransform(progress, (p) => Math.min(1, Math.max(0, p * N - k)));
  return (
    <button type="button" role="tab" aria-selected={active} className={`hc-tab ${x.key} ${active ? "on" : ""}`} onClick={onClick}>
      <span className="t">{x.label}</span>
      <span className="num">{h ? x.format(x.pick(h)) : "—"}</span>
      <span className="track"><motion.i className="fill" style={{ scaleX: fill }} /></span>
    </button>
  );
}

export function HeroCarousel({ h }: { h: Highlights | null }) {
  const [i, setI] = useState(0);
  const [dir, setDir] = useState(1);
  const reduce = useReducedMotion();
  const root = useRef<HTMLElement>(null);
  const { scrollYProgress } = useScroll({ target: root, offset: ["start start", "end end"] });
  useMotionValueEvent(scrollYProgress, "change", (p) => {
    const n = Math.min(N - 1, Math.max(0, Math.floor(p * N)));
    if (n !== i) { setDir(n > i ? 1 : -1); setI(n); }
  });
  /** Scroll to slide n (or past the hero when n === N). */
  const go = useCallback((n: number) => {
    const el = root.current;
    if (!el) return;
    const top = el.getBoundingClientRect().top + window.scrollY;
    const span = el.offsetHeight - window.innerHeight; // scrollable distance while pinned
    const target = n >= N ? top + el.offsetHeight : top + (span * Math.max(0, n)) / N + 2;
    window.scrollTo({ top: target, behavior: reduce ? "auto" : "smooth" });
  }, [reduce]);
  const next = () => go(i + 1);
  const prev = () => go(i - 1);
  const cueFade = useTransform(scrollYProgress, [0.9, 1], [1, 0]);
  const s = SLIDES[i];
  const enter = (d: number) => ({
    initial: { opacity: 0, x: dir * 28, filter: "blur(8px)" },
    animate: { opacity: 1, x: 0, filter: "blur(0px)", transition: { duration: 0.6, ease: EASE, delay: d } },
    exit: { opacity: 0, x: dir * -20, filter: "blur(6px)", transition: { duration: 0.2, ease: EASE_IN } },
  });

  return (
    <section id="hero" ref={root} className={`s-hero hc tone-${s.key}`} aria-roledescription="carousel" aria-label="The three numbers"
      style={{ ["--slides" as string]: N }}
      onKeyDown={(e) => { if (e.key === "ArrowRight") next(); if (e.key === "ArrowLeft") prev(); }}>
      <div className="hc-pin">
        <div className="hc-glow" aria-hidden>{SLIDES.map((x) => <i key={x.key} className={`${x.key} ${x.key === s.key ? "on" : ""}`} />)}</div>

        <motion.div className="hc-stage" drag={reduce ? false : "x"} dragConstraints={{ left: 0, right: 0 }} dragElastic={0.12}
          onDragEnd={(_, info) => { if (info.offset.x < -60) next(); else if (info.offset.x > 60) prev(); }}>
          <div className="hc-copy">
            <motion.h1 initial={{ opacity: 0, y: 24, filter: "blur(10px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }} transition={{ duration: 0.9, ease: EASE }}>
              Every dollar,<br />
              <span className="hc-word">
                <AnimatePresence mode="wait" initial={false}>
                  <motion.span key={s.word} className={`w ${s.key}`} initial={{ opacity: 0, y: "0.3em", filter: "blur(10px)" }}
                    animate={{ opacity: 1, y: 0, filter: "blur(0px)", transition: { duration: 0.6, ease: EASE } }}
                    exit={{ opacity: 0, y: "-0.2em", filter: "blur(8px)", transition: { duration: 0.2, ease: EASE_IN } }}>{s.word}</motion.span>
                </AnimatePresence>
              </span>
            </motion.h1>
            <div className="hc-text" aria-live="polite">
              <AnimatePresence mode="wait" initial={false}>
                <motion.div key={s.key}>
                  <motion.span className={`hc-kicker ${s.key}`} {...enter(0)}>{s.label}</motion.span>
                  <motion.h2 {...enter(0.05)}>{s.title}</motion.h2>
                  <motion.p {...enter(0.1)}>{s.body}</motion.p>
                  <motion.div {...enter(0.15)}><Link href={`/sign-in?next=${encodeURIComponent(s.href)}`} className="hc-cta">{s.cta}<Arrow /></Link></motion.div>
                </motion.div>
              </AnimatePresence>
            </div>
          </div>

          <div className="hc-card-wrap">
            <AnimatePresence mode="wait" initial={false}>
              <motion.div key={s.key} className={`hc-card ${s.key}`}
                initial={{ opacity: 0, y: 24, scale: 0.97, rotateX: 6 }} animate={{ opacity: 1, y: 0, scale: 1, rotateX: 0, transition: { duration: 0.7, ease: EASE } }}
                exit={{ opacity: 0, y: -12, scale: 0.98, transition: { duration: 0.2, ease: EASE_IN } }}>
                <span className="k">{s.label}</span>
                <CountUp className="v" value={h ? s.pick(h) : null} format={s.format} duration={1.2} />
                <span className="n">{s.note}</span>
                <s.Visual h={h} />
              </motion.div>
            </AnimatePresence>
          </div>
        </motion.div>

        <div className="hc-controls">
          <div className="hc-tabs" role="tablist" aria-label="Choose a number">
            {SLIDES.map((x, k) => <Tab key={x.key} k={k} active={k === i} progress={scrollYProgress} h={h} onClick={() => go(k)} />)}
          </div>
          <div className="hc-arrows">
            <button type="button" className="hc-arrow" onClick={prev} disabled={i === 0} aria-label="Previous number">
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden><path d="M10 3.5 5.5 8l4.5 4.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </button>
            <button type="button" className="hc-arrow" onClick={next} disabled={i === N - 1} aria-label="Next number">
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden><path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </button>
          </div>
        </div>

        <motion.button type="button" className="hc-cue" onClick={() => go(i < N - 1 ? i + 1 : N)} style={{ opacity: cueFade }}>
          <span>{i < N - 1 ? "Scroll for the next number" : "Why it matters now"}</span>
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden><path d="M3 5.5 7 9.5l4-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </motion.button>
      </div>
    </section>
  );
}

const Arrow = () => (
  <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden><path d="M2 6h7.5M6.5 2.5 10 6l-3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>
);
