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

// ------------------------------------------------------------------------------------------------ power-grid ambient
// A seeded lattice of "substations" joined by lines, with current pulses running along a few routes. Seeded so the
// server and client render identical markup.
function rng(seed: number) {
  return () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };
}
const COLS = 15, ROWS = 8, W = 1200, H = 520;
const GRID = (() => {
  const r = rng(7);
  const nodes = Array.from({ length: COLS * ROWS }, (_, k) => {
    const c = k % COLS, row = Math.floor(k / COLS);
    return { x: (c / (COLS - 1)) * W + (r() - 0.5) * 34, y: (row / (ROWS - 1)) * H + (r() - 0.5) * 30, hub: r() < 0.07 };
  });
  const at = (c: number, row: number) => nodes[row * COLS + c];
  const edges: string[] = [];
  for (let row = 0; row < ROWS; row++) for (let c = 0; c < COLS; c++) {
    const a = at(c, row);
    if (c < COLS - 1 && r() < 0.86) { const b = at(c + 1, row); edges.push(`M${a.x.toFixed(1)} ${a.y.toFixed(1)}L${b.x.toFixed(1)} ${b.y.toFixed(1)}`); }
    if (row < ROWS - 1 && r() < 0.62) { const b = at(c, row + 1); edges.push(`M${a.x.toFixed(1)} ${a.y.toFixed(1)}L${b.x.toFixed(1)} ${b.y.toFixed(1)}`); }
  }
  // Routes: random walks left to right, the paths current pulses follow.
  const routes = Array.from({ length: 9 }, () => {
    let row = Math.floor(r() * ROWS), c = 0;
    const pts = [at(c, row)];
    while (c < COLS - 1) {
      if (r() < 0.7 || row <= 0 || row >= ROWS - 1) c++; else row += r() < 0.5 ? -1 : 1;
      pts.push(at(c, row));
    }
    return { d: pts.map((p, n) => `${n ? "L" : "M"}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(""), dur: 5 + r() * 6, delay: -r() * 10 };
  });
  return { nodes, edges, routes };
})();

export function GridNetwork({ className }: { className?: string }) {
  return (
    <div className={`gridnet ${className ?? ""}`} aria-hidden>
      <svg viewBox={`-20 -20 ${W + 40} ${H + 40}`} preserveAspectRatio="xMidYMid slice">
        <defs>
          <linearGradient id="gn-pulse" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0" stopColor="#4c8dff" stopOpacity="0" />
            <stop offset="0.6" stopColor="#7fb0ff" />
            <stop offset="1" stopColor="#ffffff" />
          </linearGradient>
        </defs>
        <path d={GRID.edges.join("")} className="gn-edge" />
        {GRID.routes.map((rt, k) => (
          <path key={k} d={rt.d} pathLength={1} className="gn-pulse" stroke="url(#gn-pulse)"
            style={{ animationDuration: `${rt.dur.toFixed(2)}s`, animationDelay: `${rt.delay.toFixed(2)}s` }} />
        ))}
        {GRID.nodes.map((n, k) => n.hub
          ? <circle key={k} cx={n.x} cy={n.y} r={3.2} className="gn-hub" style={{ animationDelay: `${(-k % 7) * 0.6}s` }} />
          : <circle key={k} cx={n.x} cy={n.y} r={1.3} className="gn-node" />)}
      </svg>
    </div>
  );
}
