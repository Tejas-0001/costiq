"use client";
// CostIQ motion identity (Premium): one signature curve, three durations, one entrance (rise + fade + unblur).
import { animate, AnimatePresence, motion, useInView, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState, type ReactNode } from "react";

export const EASE = [0.22, 1, 0.36, 1] as const; // ease-out-quint: fast start, long gentle landing
export const EASE_IN = [0.4, 0, 1, 1] as const; // exits
export const DUR = { quick: 0.16, standard: 0.5, slow: 0.9 } as const;

/** Rises into place the first time it scrolls into view. */
export function Reveal({ children, delay = 0, y = 24, className, id, as = "div" }: {
  children: ReactNode; delay?: number; y?: number; className?: string; id?: string; as?: "div" | "li" | "section" | "p" | "h2";
}) {
  const M = motion[as];
  return (
    <M id={id} className={className} initial={{ opacity: 0, y, filter: "blur(8px)" }} whileInView={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      viewport={{ once: true, margin: "0px 0px -12% 0px" }} transition={{ duration: DUR.standard + 0.2, ease: EASE, delay }}>
      {children}
    </M>
  );
}

/** Counts from zero to `value` when it first becomes visible (instant under reduced motion). */
export function CountUp({ value, format, duration = 1.6, className }: {
  value: number | null | undefined; format: (n: number) => string; duration?: number; className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const seen = useInView(ref, { once: true, margin: "0px 0px -10% 0px" });
  const reduce = useReducedMotion();
  const [shown, setShown] = useState(0);
  useEffect(() => {
    if (!seen || value == null || reduce) return;
    const c = animate(0, value, { duration, ease: EASE, onUpdate: setShown });
    return () => c.stop();
  }, [seen, value, reduce, duration]);
  return <span ref={ref} className={className} style={{ fontVariantNumeric: "tabular-nums" }}>{value == null ? "—" : format(reduce ? value : shown)}</span>;
}

/** Cycles through words in place. The slot eases to each word's width, so a centered line re-centers smoothly. */
export function RotatingWord({ words, interval = 2600, className }: { words: string[]; interval?: number; className?: string }) {
  const [i, setI] = useState(0);
  const [widths, setWidths] = useState<number[] | null>(null);
  const sizer = useRef<HTMLSpanElement>(null);
  const reduce = useReducedMotion();
  useEffect(() => {
    if (reduce) return;
    const t = setInterval(() => setI((n) => (n + 1) % words.length), interval);
    return () => clearInterval(t);
  }, [words.length, interval, reduce]);
  // Measure every word (hidden copies); re-measures when the web font loads or the headline resizes.
  useEffect(() => {
    const el = sizer.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidths([...el.children].map((c) => (c as HTMLElement).getBoundingClientRect().width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return (
    <motion.span className={`rot ${className ?? ""}`} aria-live="off" initial={false}
      animate={widths ? { width: widths[i] } : undefined} transition={{ duration: DUR.slow, ease: EASE }}>
      <span ref={sizer} className="rot-sizer" aria-hidden>{words.map((w) => <span key={w}>{w}</span>)}</span>
      <AnimatePresence mode="wait" initial={false}>
        <motion.span key={words[i]} className="rot-word"
          initial={{ opacity: 0, y: "0.3em", filter: "blur(10px)" }}
          animate={{ opacity: 1, y: 0, filter: "blur(0px)", transition: { duration: DUR.slow * 0.8, ease: EASE } }}
          exit={{ opacity: 0, y: "-0.2em", filter: "blur(10px)", transition: { duration: DUR.standard * 0.6, ease: EASE_IN } }}>
          {words[i]}
        </motion.span>
      </AnimatePresence>
    </motion.span>
  );
}
