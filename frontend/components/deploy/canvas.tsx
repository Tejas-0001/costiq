"use client";
// The live "impact" summary: footprint metrics, how much of the stack the client already owns, weeks to production and
// a compact architecture map whose chips are green when they're the client's ("yours") and blue when we provide them.
import { animate, AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useRef } from "react";
import { EASE } from "@/components/motion";
import type { Node, Plan } from "./model";

const whole = (n: number) => Math.round(n).toLocaleString("en-US");

/** A number that tweens from its previous value. Pass a stable (module-level) format function. */
export function Num({ value, format = whole }: { value: number; format?: (n: number) => string }) {
  const ref = useRef<HTMLSpanElement>(null);
  const prev = useRef(value);
  const reduce = useReducedMotion();
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (reduce) { el.textContent = format(value); prev.current = value; return; }
    const c = animate(prev.current, value, { duration: 0.9, ease: EASE, onUpdate: (v) => { el.textContent = format(v); } });
    prev.current = value;
    return () => c.stop();
  }, [value, reduce, format]);
  return <span ref={ref}>{format(value)}</span>;
}

export type Delta = { id: number; key: string; text: string; up: boolean };

export const gb = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)} TB` : `${Math.round(n)} GB`);

/** Placeholder for a value the answers don't determine yet. */
const Pending = () => <span className="pending" aria-label="Not yet known">—</span>;

export function Meters({ plan, deltas, show = { size: true, servers: true } }: { plan: Plan; deltas: Delta[]; show?: { size: boolean; servers: boolean } }) {
  const k8s = plan.answers.platform === "k8s";
  const cells: { key: string; label: string; value: number; fmt?: (n: number) => string; unit?: string }[] = [
    { key: "vcpu", label: "vCPU", value: plan.totals.vcpu },
    { key: "ram", label: "Memory", value: plan.totals.ram, unit: "GB" },
    { key: "storage", label: "Storage", value: plan.totals.storage, fmt: gb },
    { key: "servers", label: k8s ? "New servers" : "Servers", value: plan.totals.servers },
  ];
  return (
    <div className="dp-meters">
      {cells.map((c) => (
        <div key={c.key} className="dp-meter">
          <span className="lbl">{c.label}</span>
          {(c.key === "servers" ? show.servers : show.size)
            ? <motion.b initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5, ease: EASE }}>
                <Num value={c.value} format={c.fmt} />{c.unit && <small>{c.unit}</small>}</motion.b>
            : <b><Pending /></b>}
          <AnimatePresence>
            {deltas.filter((d) => d.key === c.key).map((d) => (
              <motion.em key={d.id} className={d.up ? "up" : "down"} initial={{ opacity: 0, y: 4, scale: 0.9 }}
                animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: -8 }} transition={{ duration: 0.4, ease: EASE }}>
                {d.text}
              </motion.em>
            ))}
          </AnimatePresence>
        </div>
      ))}
    </div>
  );
}

/** How much of the stack is already the client's: a bar that fills green as they tell us what they have. */
export function ReuseBar({ have, total, show = true }: { have: number; total: number; show?: boolean }) {
  const pct = show && total ? (have / total) * 100 : 0;
  return (
    <div className="dp-reuse">
      <div className="hd">{show ? <><b><Num value={have} /></b><span>of {total} pieces are yours</span></> : <><b><Pending /></b><span>pieces you already own</span></>}</div>
      <div className={`bar ${show ? "" : "pending"}`}><motion.i initial={false} animate={{ width: `${pct}%` }} transition={{ duration: 0.8, ease: EASE }} /></div>
    </div>
  );
}

export function Weeks({ plan, show = true }: { plan: Plan; show?: boolean }) {
  const max = Math.max(...plan.timeline.map((p) => p.start + p.weeks), 1);
  return (
    <div className="dp-weeks">
      <div className="hd"><b>{show ? <Num value={plan.weeks} /> : <Pending />}</b><span>weeks to production</span></div>
      <div className="bars">
        {show && plan.timeline.map((p, i) => (
          <motion.i key={p.phase} title={`${p.phase}: ${p.weeks} wk`} initial={false} className={`p${i}`}
            animate={{ left: `${(p.start / max) * 100}%`, width: `${(p.weeks / max) * 100}%` }} transition={{ duration: 0.8, ease: EASE }} />
        ))}
      </div>
    </div>
  );
}

const LANES: { id: Node["lane"]; label: string }[] = [
  { id: "people", label: "People" },
  { id: "edge", label: "Edge" },
  { id: "platform", label: "App" },
  { id: "data", label: "Data" },
  { id: "operate", label: "Operate" },
  { id: "sources", label: "Sources" },
];

function Chip({ n, pulse }: { n: Node; pulse: boolean }) {
  return (
    <motion.span layout="position" className={`dp-chip ${n.owner}`} title={`${n.label}: ${n.sub} (${n.owner === "yours" ? "yours" : "we provide"})`}
      initial={{ opacity: 0, scale: 0.85 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.85, transition: { duration: 0.15 } }}
      transition={{ duration: 0.4, ease: EASE }}>
      {pulse && <motion.span className="ring" initial={{ opacity: 0.7, scale: 1 }} animate={{ opacity: 0, scale: 1.25 }} transition={{ duration: 0.9, ease: EASE }} />}
      <motion.i className="dot" initial={false} animate={{ backgroundColor: n.owner === "yours" ? "#12924f" : "#2f6bff" }} transition={{ duration: 0.3 }} />
      {n.label}
      {n.count != null && n.count > 1 && <em>×{n.count}</em>}
    </motion.span>
  );
}

/** Compact architecture map: one row per lane on a rail with current running down it. */
export function Diagram({ plan, changed }: { plan: Plan; changed: string[] }) {
  const runtime = plan.answers.platform === "k8s" ? "Kubernetes" : plan.answers.platform === "vms" ? "Docker on your VMs" : "Hardened servers";
  return (
    <div className="dp-map">
      <div className="dp-map-hd"><b>Architecture</b><span><i className="dot yours" />yours<i className="dot provided" />we provide</span></div>
      <div className="dp-map-rows">
        <span className="rail" aria-hidden><i /></span>
        <AnimatePresence initial={false}>
        {plan.nodes.length === 0 && (
          <motion.p key="empty" className="dp-map-empty" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, transition: { duration: 0.15 } }}>
            Nothing drawn yet. Each answer adds the pieces it decides.
          </motion.p>
        )}
        {LANES.filter((lane) => plan.nodes.some((n) => n.lane === lane.id)).map((lane) => {
          const all = plan.nodes.filter((n) => n.lane === lane.id);
          // sources can run to ten; show three and a count so the map keeps its height
          const nodes = lane.id === "sources" && all.length > 4 ? all.slice(0, 3) : all;
          const more = all.length - nodes.length;
          return (
            <motion.div key={lane.id} className={`row ${lane.id}`} layout="position" initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }} transition={{ duration: 0.45, ease: EASE }}>
              <span className="k"><i />{lane.id === "platform" ? runtime : lane.label}</span>
              <motion.div layout className="chips">
                <AnimatePresence initial={false}>
                  {nodes.length === 0 && <motion.span key="none" className="none" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>None selected</motion.span>}
                  {nodes.map((n) => <Chip key={n.id} n={n} pulse={changed.includes(n.id)} />)}
                  {more > 0 && <motion.span key="more" layout="position" className="dp-chip more" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>+{more} more</motion.span>}
                </AnimatePresence>
              </motion.div>
            </motion.div>
          );
        })}
        </AnimatePresence>
      </div>
    </div>
  );
}
