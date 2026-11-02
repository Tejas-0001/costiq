"use client";
// Guided on-prem deployment planner: one question at a time. Every option previews what it would change before you pick
// it, the live plan on the right redraws as you answer, and the last step produces the blueprint. Works as an overlay
// on the home page and as the /deploy page.
import { AnimatePresence, motion, MotionConfig } from "motion/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Mark } from "@/components/brand";
import { EASE, EASE_IN } from "@/components/motion";
import { Blueprint, decodeAnswers } from "./blueprint";
import { Diagram, gb, Meters, ReuseBar, Weeks, type Delta } from "./canvas";
import { BLOCKS, CARD, CHAPTERS, DEFAULTS, normalize, plan as makePlan, QUESTIONS, type Answers, type Key, type Plan } from "./model";
import { CUSTODY, CUTOVERS, ROUTES } from "./release";
import { RouteFlow, TrafficSplit } from "./release-viz";

type Phase = "intro" | "ask" | "build" | "blueprint";
const TYPICAL: Answers = { ...DEFAULTS, have: BLOCKS.map((b) => b.id) };

// Unanswered questions fall back to this baseline for the internal calculation only; the UI never shows it as an answer.
const BASE: Answers = { ...DEFAULTS, have: [], compliance: [], sources: [] };
const full = (a: Partial<Answers>) => normalize({ ...BASE, ...a });

/** Which parts of the plan the answers so far actually determine (the live panel shows only those). */
function reveal(known: (k: Key) => boolean) {
  const nodeShown: Record<string, boolean> = {
    users: known("users"), idp: known("identity"),
    firewall: known("have"), waf: known("have"), lb: known("have"), gateway: known("have"), pki: known("have"),
    proxy: known("have") && known("egress"),
    web: known("platform"), api: known("platform"), ingest: known("platform"),
    db: known("database"), storage: known("have"), backup: known("have"),
    monitoring: known("have"), siem: known("have"), registry: known("have"), vault: known("have"), cicd: known("have"),
    edr: known("have"), pam: known("have"), itsm: known("have"),
  };
  return {
    size: known("users") && known("spend"),
    servers: known("platform") && known("database"),
    weeks: known("platform"),
    reuse: known("have"),
    node: (id: string) => (id.startsWith("src-") ? known("sources") : !!nodeShown[id]),
  };
}

const fmtStorage = (n: number) => (Math.abs(n) >= 1000 ? `${(Math.abs(n) / 1000).toFixed(1)} TB` : `${Math.round(Math.abs(n))} GB`);

function deltasBetween(a: Plan, b: Plan, seq: number): Delta[] {
  const out: Delta[] = [];
  const add = (key: string, d: number, unit: string, fmt = (n: number) => `${Math.round(Math.abs(n))}`) => {
    if (Math.abs(d) >= 0.5) out.push({ id: seq * 10 + out.length, key, text: `${d > 0 ? "+" : "−"}${fmt(d)}${unit}`, up: d > 0 });
  };
  add("vcpu", b.totals.vcpu - a.totals.vcpu, "");
  add("ram", b.totals.ram - a.totals.ram, " GB");
  add("storage", b.totals.storage - a.totals.storage, "", fmtStorage);
  add("servers", b.totals.servers - a.totals.servers, "");
  return out;
}

/** What choosing an option would change, as short chips ("+4 vCPU", "−2 wk", "1 less to run"). */
/** Metrics the live plan is currently showing; option chips may only talk about these, so the two never disagree. */
type Shown = { size: boolean; servers: boolean; weeks: boolean; reuse: boolean };
const anyShown = (m: Shown) => m.size || m.servers || m.weeks || m.reuse;

function impactChips(cur: Plan, next: Plan, m: Shown): { text: string; tone: "up" | "down" | "good" }[] {
  const chips: { text: string; tone: "up" | "down" | "good" }[] = [];
  const d = (x: number) => (x > 0 ? "+" : "−");
  const sv = next.totals.servers - cur.totals.servers;
  if (sv && m.servers) chips.push({ text: `${d(sv)}${Math.abs(sv)} server${Math.abs(sv) === 1 ? "" : "s"}`, tone: sv > 0 ? "up" : "down" });
  const cpu = next.totals.vcpu - cur.totals.vcpu;
  if (Math.abs(cpu) >= 1 && m.size) chips.push({ text: `${d(cpu)}${Math.round(Math.abs(cpu))} vCPU`, tone: cpu > 0 ? "up" : "down" });
  const st = next.totals.storage - cur.totals.storage;
  if (Math.abs(st) >= 10 && m.size) chips.push({ text: `${d(st)}${fmtStorage(st)}`, tone: st > 0 ? "up" : "down" });
  const wk = next.weeks - cur.weeks;
  if (wk && m.weeks) chips.push({ text: `${Math.abs(wk)} wk ${wk > 0 ? "longer" : "sooner"}`, tone: wk > 0 ? "up" : "down" });
  const reuse = next.reuse.have - cur.reuse.have;
  void reuse; // the card's own "Yours / Missing" line already says this
  return chips.slice(0, 3);
}

function changedNodes(a: Plan, b: Plan): string[] {
  const before = new Map(a.nodes.map((n) => [n.id, `${n.owner}|${n.label}|${n.count}|${n.sub}`]));
  return b.nodes.filter((n) => before.get(n.id) !== `${n.owner}|${n.label}|${n.count}|${n.sub}`).map((n) => n.id);
}

const Check = () => (
  <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden><path d="M2.5 6.3 5 8.7l4.5-5.2" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" /></svg>
);

// ---- routing: each step has its own URL (/deploy, /deploy?q=<question>, /deploy?view=blueprint) so Back, Forward and
// refresh work; answers live in this tab's sessionStorage so a refresh keeps them.
const STORE = "costiq-deploy-planner";
type Saved = { answers: Partial<Answers>; done: Key[] };
function loadSaved(): Saved | null {
  try { const v = sessionStorage.getItem(STORE); return v ? (JSON.parse(v) as Saved) : null; } catch { return null; }
}
function saveState(v: Saved | null) {
  try { if (v) sessionStorage.setItem(STORE, JSON.stringify(v)); else sessionStorage.removeItem(STORE); } catch { /* storage unavailable */ }
}
function fromUrl(): { phase: Phase; step: number } {
  const sp = new URLSearchParams(location.search);
  if (sp.get("view") === "blueprint") return { phase: "blueprint", step: 0 };
  const i = QUESTIONS.findIndex((x) => x.key === sp.get("q"));
  return i >= 0 ? { phase: "ask", step: i } : { phase: "intro", step: 0 };
}
const urlFor = (phase: Phase, step: number) => (phase === "ask" ? `?q=${QUESTIONS[step].key}` : phase === "blueprint" ? "?view=blueprint" : "");

export function Planner({ onClose, initial }: { onClose?: () => void; initial?: Partial<Answers> | null }) {
  // client-only (the /deploy page mounts it after reading the URL), so the initializers may read location and storage
  const boot = useMemo(() => {
    if (initial) return { answers: full(initial) as Partial<Answers>, done: QUESTIONS.map((q) => q.key), phase: "blueprint" as Phase, step: 0 };
    const saved = loadSaved();
    const at = fromUrl();
    // a blueprint URL with nothing answered has nothing to show; start at the beginning instead
    const phase = at.phase === "blueprint" && !saved ? "intro" : at.phase;
    return { answers: saved?.answers ?? {}, done: saved?.done ?? [], phase, step: at.step };
  }, [initial]);
  // only what the person has actually answered; the planner starts empty
  const [answers, setAnswers] = useState<Partial<Answers>>(boot.answers);
  const [phase, setPhase] = useState<Phase>(boot.phase);
  const [step, setStep] = useState(boot.step); // index into QUESTIONS
  const [done, setDone] = useState<Set<Key>>(() => new Set(boot.done));
  const [deltas, setDeltas] = useState<Delta[]>([]);
  const [changed, setChanged] = useState<string[]>([]);
  const seq = useRef(0);
  const effective = useMemo(() => full(answers), [answers]);
  const plan = useMemo(() => makePlan(effective), [effective]);
  const finish = useCallback(() => setPhase("blueprint"), []);

  const skipped = (i: number) => QUESTIONS[i].skip?.(effective) ?? null;
  const knownIn = (a: Partial<Answers>, eff: Answers) => (k: Key) =>
    a[k] !== undefined || !!QUESTIONS.find((x) => x.key === k)?.skip?.(eff);
  const known = knownIn(answers, effective);
  const shown = reveal(known);

  const apply = useCallback((next: Partial<Answers>) => {
    const eff = full(next);
    // keep the person's answers in step with normalization (e.g. a choice a later answer has ruled out)
    const synced: Partial<Answers> = {};
    for (const k of Object.keys(next) as Key[]) (synced as Record<Key, unknown>)[k] = eff[k];
    const before = plan, after = makePlan(eff);
    seq.current += 1;
    // deltas only for meters that were already showing numbers
    const wasShown = reveal(knownIn(answers, effective));
    setDeltas(deltasBetween(before, after, seq.current).filter((d) => (d.key === "servers" ? wasShown.servers : wasShown.size)));
    setChanged(changedNodes(before, after));
    setAnswers(synced);
    const s = seq.current;
    window.setTimeout(() => { if (seq.current === s) { setDeltas([]); setChanged([]); } }, 2400);
  }, [answers, effective, plan]);

  const q = QUESTIONS[step];
  const nextIndex = (from: number) => { let i = from + 1; while (i < QUESTIONS.length && skipped(i)) i++; return i; };
  const prevIndex = (from: number) => { let i = from - 1; while (i >= 0 && skipped(i)) i--; return i; };
  const visible = QUESTIONS.map((_, i) => i).filter((i) => !skipped(i));
  const answered = q ? answers[q.key] !== undefined : false;
  const canContinue = !!q && (q.multi || answered);

  const toggled = (value: string): Partial<Answers> => {
    if (q.multi) {
      const cur = (answers[q.key] as string[] | undefined) ?? [];
      return { ...answers, [q.key]: cur.includes(value) ? cur.filter((x) => x !== value) : [...cur, value] };
    }
    return { ...answers, [q.key]: value };
  };
  const choose = (value: string) => { if (q) apply(toggled(value)); };
  const advance = () => {
    if (!canContinue) return;
    if (q.multi && answers[q.key] === undefined) apply({ ...answers, [q.key]: [] }); // "none of these" is an answer too
    setDone((d) => new Set(d).add(q.key));
    const n = nextIndex(step);
    if (n >= QUESTIONS.length) { setPhase("build"); return; }
    setStep(n);
  };
  const back = () => { const p = prevIndex(step); if (p >= 0) setStep(p); else setPhase("intro"); };
  const editAt = (i: number) => { setPhase("ask"); setStep(i); };
  const goChapter = (c: string) => {
    const i = visible.find((x) => QUESTIONS[x].chapter === c);
    if (i != null) editAt(i);
  };

  // keyboard: 1-9 picks an option, Enter continues, Escape closes
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && onClose) { onClose(); return; }
      if (phase !== "ask" || !q || (e.target as HTMLElement)?.tagName === "INPUT") return;
      if (e.key === "Enter") { e.preventDefault(); advance(); return; }
      const k = Number(e.key);
      if (k >= 1 && k <= q.options.length && !q.blocked?.(q.options[k - 1].value, effective)) choose(q.options[k - 1].value);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => { window.scrollTo({ top: 0, behavior: "smooth" }); }, [step, phase]);

  // state -> URL (a new history entry per step; the build animation has no URL of its own)
  useEffect(() => {
    if (phase === "build") return;
    const search = urlFor(phase, step);
    if (location.search !== search) {
      history.pushState(null, "", `${location.pathname}${search}${phase === "blueprint" ? location.hash : ""}`);
    }
  }, [phase, step]);
  // URL -> state (Back / Forward)
  useEffect(() => {
    const onPop = () => { const at = fromUrl(); setPhase(at.phase); setStep(at.step); };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  // keep answers across a refresh
  useEffect(() => { saveState({ answers, done: [...done] }); }, [answers, done]);

  const chapterIdx = q ? CHAPTERS.indexOf(q.chapter) : 0;
  const answeredCount = QUESTIONS.filter((x) => done.has(x.key)).length;
  const progress = phase === "blueprint" || phase === "build" ? 1 : answeredCount / QUESTIONS.length;
  const reached = phase === "blueprint" ? CHAPTERS.length : chapterIdx;
  const prior = prevIndex(step);
  // header: only the parts of "where · how · sites" that have been answered
  const [where, runtime, sites] = plan.topology.split(" · ");
  const title = [known("hosting") && where, known("platform") && runtime, known("availability") && sites].filter(Boolean).join(" · ");

  return (
    <MotionConfig reducedMotion="user">
      <div className="dp" ref={scroller}>
        <header className="dp-top">
          <div className="dp-title"><Mark size={22} /><b>Deployment planner</b><span>On-prem · self-hosted</span></div>
          <ol className="dp-chapters" aria-label="Chapters">
            {CHAPTERS.map((c, i) => {
              const state = phase === "blueprint" || phase === "build" || i < chapterIdx ? "done" : i === chapterIdx && phase === "ask" ? "on" : "";
              return (
                <li key={c} className={state}>
                  <button type="button" onClick={() => goChapter(c)} disabled={phase === "intro" || i > reached}>
                    <i>{state === "done" ? <Check /> : i + 1}</i><span>{c}</span>
                  </button>
                </li>
              );
            })}
            <li className={phase === "blueprint" ? "on" : ""}><button type="button" disabled><i>{CHAPTERS.length + 1}</i><span>Blueprint</span></button></li>
          </ol>
          <div className="dp-top-r">
            {phase !== "intro" && <button type="button" className="dp-link" onClick={() => { apply({}); setDone(new Set()); setStep(0); setPhase("intro"); saveState(null); }}>Start over</button>}
            {onClose && <button type="button" className="dp-x" onClick={onClose} aria-label="Close planner">
              <svg width="14" height="14" viewBox="0 0 14 14"><path d="M2 2l10 10M12 2 2 12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg></button>}
          </div>
          <motion.span className="dp-progress" initial={false} animate={{ scaleX: progress }} transition={{ duration: 0.7, ease: EASE }} />
        </header>

        <AnimatePresence mode="wait" initial={false}>
          {phase === "blueprint" ? (
            <motion.div key="bp" initial={{ opacity: 0, y: 24 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.6, ease: EASE }}>
              <Blueprint plan={plan} onEdit={() => editAt(0)} />
            </motion.div>
          ) : phase === "build" ? (
            <Build key="build" plan={plan} onDone={finish} />
          ) : (
            <motion.div key="ask" className="dp-main" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, transition: { duration: 0.2 } }}>
              <div className="dp-stage">
                {phase === "intro" ? (
                  <Intro onStart={() => { setPhase("ask"); setStep(0); }}
                    onTypical={() => { apply({ ...TYPICAL }); setDone(new Set(QUESTIONS.map((x) => x.key))); setPhase("build"); }} />
                ) : (
                  <>
                    <AnimatePresence mode="wait">
                      <QuestionCard key={q.key} index={step} pos={visible.indexOf(step) + 1} total={visible.length} answers={answers} effective={effective}
                        plan={plan} answered={answered} canContinue={canContinue}
                        metrics={{ size: shown.size, servers: shown.servers, weeks: shown.weeks, reuse: shown.reuse || q.key === "have" }}
                        notes={QUESTIONS.map((_, i) => i).filter((i) => i > prior && i < step).map((i) => skipped(i)?.note).filter((x): x is string => !!x)}
                        preview={(v) => makePlan(full(toggled(v)))} onChoose={choose} onNext={advance} onBack={back}
                        isLast={nextIndex(step) >= QUESTIONS.length} setAll={(vals) => apply({ ...answers, [q.key]: vals })} />
                    </AnimatePresence>
                    <Trail answers={effective} upTo={step} visible={visible} onEdit={editAt} />
                  </>
                )}
              </div>
              <aside className="dp-side" aria-label="Live plan">
                <div className="dp-side-in">
                  <div className="dp-side-hd">
                    <span className="k"><span className="live" />Live plan</span>
                    <AnimatePresence mode="wait" initial={false}>
                      <motion.h3 key={title} initial={{ opacity: 0, y: 6, filter: "blur(4px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                        exit={{ opacity: 0, y: -6, filter: "blur(4px)" }} transition={{ duration: 0.35, ease: EASE }} className={title ? "" : "empty"}>
                        {title || "Your plan builds as you answer."}</motion.h3>
                    </AnimatePresence>
                  </div>
                  <Meters plan={plan} deltas={deltas} show={{ size: shown.size, servers: shown.servers }} />
                  <div className="dp-side-row">
                    <ReuseBar have={plan.reuse.have} total={plan.reuse.total} show={shown.reuse} />
                    <Weeks plan={plan} show={shown.weeks} />
                  </div>
                  <Diagram plan={{ ...plan, nodes: plan.nodes.filter((n) => shown.node(n.id)) }} changed={changed} />
                </div>
              </aside>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </MotionConfig>
  );
}

function Intro({ onStart, onTypical }: { onStart: () => void; onTypical: () => void }) {
  const rise = (d: number) => ({ initial: { opacity: 0, y: 20, filter: "blur(8px)" }, animate: { opacity: 1, y: 0, filter: "blur(0px)" }, transition: { duration: 0.7, ease: EASE, delay: d } });
  return (
    <div className="dp-intro">
      <motion.div {...rise(0.05)} className="dp-guide"><Mark size={30} /></motion.div>
      <motion.h2 {...rise(0.12)}>Let&apos;s plan your deployment, together.</motion.h2>
      <motion.p {...rise(0.2)} className="lead">
        About twenty-five quick questions. Tell us what you already run and we&apos;ll plug into it: if you have an F5 or a Cisco firewall, we use
        them. Every option shows what it would change before you pick it.
      </motion.p>
      <motion.ul {...rise(0.28)} className="dp-intro-list">
        <li><b>Hardware</b> per environment, sized to your users and spend</li>
        <li><b>Infrastructure and software</b>, with what&apos;s yours and what we bring</li>
        <li><b>Firewall rules, F5 and Cisco configuration, identity and data connections</b></li>
        <li><b>Cyber defense</b> layer by layer, tested against real attack techniques</li>
        <li><b>Compliance controls</b> checked against NERC, NIST, CISA and ISO texts</li>
        <li><b>Quality gates, code custody and the release route</b> from first commit to production</li>
        <li><b>Development plan, team and a prerequisites checklist</b> you can hand to IT</li>
      </motion.ul>
      <motion.div {...rise(0.36)} className="dp-intro-cta">
        <motion.button type="button" className="dp-btn primary" whileTap={{ scale: 0.97 }} onClick={onStart}>Start planning<span className="enter">↵</span></motion.button>
        <button type="button" className="dp-btn" onClick={onTypical}>Skip to a typical utility blueprint</button>
      </motion.div>
    </div>
  );
}

function shortAnswer(i: number, a: Answers): string {
  const q = QUESTIONS[i];
  const v = a[q.key];
  if (Array.isArray(v)) return v.length ? `${v.length} selected` : "None";
  return q.options.find((o) => o.value === v)?.label ?? String(v);
}

/** Earlier answers as small chips; click one to change it. */
function Trail({ answers, upTo, visible, onEdit }: { answers: Answers; upTo: number; visible: number[]; onEdit: (i: number) => void }) {
  const prior = visible.filter((i) => i < upTo);
  if (!prior.length) return null;
  return (
    <motion.div className="dp-trail" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.4, delay: 0.2 }}>
      <span className="lbl">Your answers</span>
      <div className="chips">
        {prior.map((i) => (
          <button key={QUESTIONS[i].key} type="button" title={`${QUESTIONS[i].ask} (click to change)`} onClick={() => onEdit(i)}>
            {shortAnswer(i, answers)}
          </button>
        ))}
      </div>
    </motion.div>
  );
}

function QuestionCard({ index, pos, total, answers, effective, plan, answered, canContinue, metrics, notes, preview, onChoose, onNext, onBack, isLast, setAll }: {
  index: number; pos: number; total: number; answers: Partial<Answers>; effective: Answers; plan: Plan; answered: boolean; canContinue: boolean; metrics: Shown;
  notes: string[]; preview: (v: string) => Plan;
  onChoose: (v: string) => void; onNext: () => void; onBack: () => void; isLast: boolean; setAll: (v: string[]) => void;
}) {
  const q = QUESTIONS[index];
  const v = answers[q.key] ?? (q.multi ? [] : undefined);
  const selected = (x: string) => (Array.isArray(v) ? v.includes(x) : v === x);
  const blockedWhy = (x: string) => q.blocked?.(x, effective) ?? null;
  const isHave = q.key === "have";
  const options = isHave
    ? q.options.filter((o) => (o.value !== "proxy" || effective.egress === "proxy") && (o.value !== "gateway" || effective.platform === "k8s"))
    : q.options.filter((o) => !blockedWhy(o.value));
  // what each option would change, against the plan as it stands right now
  const impacts: Record<string, ReturnType<typeof impactChips>> =
    Object.fromEntries(options.map((o) => {
      // a first pick has nothing honest to compare against, so single-choice chips wait until something is chosen
      if (blockedWhy(o.value) || (!q.multi && !answered)) return [o.value, []];
      // a ticked multi-select option shows what it is doing for you (without it -> with it)
      return [o.value, q.multi && selected(o.value) ? impactChips(preview(o.value), plan, metrics) : impactChips(plan, preview(o.value), metrics)];
    }));
  return (
    <motion.section className="dp-q" initial={{ opacity: 0, y: 24, filter: "blur(8px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
      exit={{ opacity: 0, y: -12, filter: "blur(6px)", transition: { duration: 0.2, ease: EASE_IN } }} transition={{ duration: 0.55, ease: EASE }}>
      <div className="dp-q-meta">
        <span className="dp-guide sm"><Mark size={18} /></span>
        <span className="chapter">{q.chapter}</span>
        <span className="count">{pos} of {total}</span>
      </div>
      <h2>{q.ask}</h2>
      <p className="why">{q.why}</p>
      {notes.map((n) => (
        <motion.p key={n} className="dp-guidenote" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4, ease: EASE, delay: 0.2 }}>
          <Mark size={16} /><span>{n}</span>
        </motion.p>
      ))}
      {!q.multi && !answered && anyShown(metrics) && <p className="dp-pick">Pick one. The other options then show what switching would change.</p>}
      {q.multi && (
        <div className="dp-bulk">
          <span><b>Choose all that apply</b> · {(v as string[]).length} of {options.length} selected</span>
          <button type="button" onClick={() => setAll(options.map((o) => o.value))}>{isHave ? "We have all of these" : "Select all"}</button>
          <button type="button" onClick={() => setAll([])}>Clear</button>
        </div>
      )}
      <div className={`dp-opts ${isHave ? "have" : ""}`} role={q.multi ? "group" : "radiogroup"} aria-label={q.ask}>
        {options.map((o, i) => {
          const why = blockedWhy(o.value);
          const on = selected(o.value);
          const card = isHave ? CARD[o.value] : undefined;
          const chips = impacts[o.value] ?? [];
          return (
            <motion.button key={o.value} type="button" role={q.multi ? "checkbox" : "radio"} aria-checked={on} disabled={!!why}
              className={`dp-opt ${on ? "on" : ""} ${why ? "blocked" : ""}`} onClick={() => onChoose(o.value)} whileTap={why ? undefined : { scale: 0.985 }}
              initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45, ease: EASE, delay: 0.06 + Math.min(i, 8) * 0.03 }}>
              {on && <motion.span layoutId={q.multi ? undefined : `sel-${q.key}`} className="dp-opt-bg" transition={{ duration: 0.4, ease: EASE }} />}
              <span className="dp-opt-hd">
                <b>{o.label}</b>
                {o.recommended && !why && <em className="rec">Most utilities</em>}
                <span className={`tick ${on ? "on" : ""} ${q.multi ? "box" : ""}`}><Check /></span>
              </span>
              {isHave && card ? (
                <AnimatePresence mode="wait" initial={false}>
                  <motion.span key={on ? "y" : "p"} className={`swap ${on ? "yours" : "provided"}`} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.22, ease: EASE }}>
                    <i>{on ? "Yours: we plug in" : "Missing: we bring it"}</i>{on ? card.yours : card.missing}
                  </motion.span>
                </AnimatePresence>
              ) : <span className="detail">{o.detail}</span>}
              {why ? <span className="reason">{why}</span> : (!q.multi && !answered) || !anyShown(metrics) || (q.multi && !chips.length) ? null : (
                <span className="impact">
                  {on && !q.multi ? <span className="cur">Your choice</span>
                    : chips.length ? <>{q.multi && <em className="lbl">{on ? "With it" : isHave ? "If you have it" : "If added"}</em>}
                      {chips.map((c) => <span key={c.text} className={c.tone}>{c.text}</span>)}</>
                    : q.multi ? null : traits(q.key, o.value) ?? <span className="none">No change to the footprint</span>}
                </span>
              )}
            </motion.button>
          );
        })}
      </div>
      <AnimatePresence mode="wait" initial={false}>
        {typeof v === "string" && PREVIEW.includes(q.key) && (
          <motion.div key={v} className="dl dp-preview" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.3, ease: EASE }}>
            <Preview k={q.key} v={v} plan={plan} />
          </motion.div>
        )}
      </AnimatePresence>
      <div className="dp-nav">
        <button type="button" className="dp-btn ghost" onClick={onBack}>Back</button>
        <span className="hint">{canContinue ? `Press 1–${Math.min(options.length, 9)} to choose · Enter to continue` : "Choose an option to continue"}</span>
        <motion.button type="button" className="dp-btn primary" onClick={onNext} disabled={!canContinue} whileTap={canContinue ? { scale: 0.97 } : undefined}>
          {isLast ? "Build my blueprint" : q.multi && (v as string[]).length === 0 ? "None of these" : "Continue"}<span className="enter">↵</span>
        </motion.button>
      </div>
    </motion.section>
  );
}

const PREVIEW: Key[] = ["custody", "route", "cutover"];

/** For delivery choices the footprint doesn't move, so the chips say what does differ between the options. */
function traits(k: Key, v: string) {
  const chips: { text: string; tone: "good" | "cur" | "up" }[] = [];
  if (k === "route" && ROUTES[v]) {
    const r = ROUTES[v];
    chips.push(r.airgap === "yes" ? { text: "Air-gap ready", tone: "good" } : r.airgap === "partial" ? { text: "One outbound rule", tone: "cur" } : { text: "Needs remote access", tone: "up" });
    chips.push({ text: r.flow.includes("|") ? "Built on our side" : "Built inside your boundary", tone: r.flow.includes("|") ? "cur" : "good" });
  }
  if (k === "cutover" && CUTOVERS[v]) chips.push({ text: CUTOVERS[v].downtime, tone: CUTOVERS[v].downtime.startsWith("No") ? "good" : "up" });
  if (k === "custody") chips.push(v === "client-repo" || v === "rebuild" ? { text: "No vendor copy needed", tone: "good" } : { text: "A copy sits outside", tone: "cur" });
  return chips.length ? <>{chips.map((c) => <span key={c.text} className={c.tone}>{c.text}</span>)}</> : null;
}

/** What the chosen option looks like in practice, under the delivery questions. */
function Preview({ k, v, plan }: { k: Key; v: string; plan: Plan }) {
  if (k === "route" && ROUTES[v]) return <><span className="dp-preview-k">How a release travels</span><RouteFlow r={ROUTES[v]} /></>;
  if (k === "custody" && CUSTODY[v]) return (
    <><span className="dp-preview-k">How it works</span><ol className="dl-steps-h">{CUSTODY[v].flow.map((s, i) => <li key={s}><i>{i + 1}</i>{s}</li>)}</ol></>
  );
  if (k === "cutover" && CUTOVERS[v]) return <><span className="dp-preview-k">On {plan.lb.via}</span><TrafficSplit c={CUTOVERS[v]} via={plan.lb.via} f5={plan.lb.f5} /></>;
  return null;
}

const BUILD_STEPS = ["Sizing hardware for each environment", "Choosing what to reuse and what to bring", "Writing the firewall, F5 and Cisco configuration",
  "Layering the cyber defenses", "Laying out the quality gates and release route",
  "Mapping identity, roles and scope", "Matching compliance controls", "Scheduling the team and timeline"];

function Build({ plan, onDone }: { plan: Plan; onDone: () => void }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) { const t = window.setTimeout(onDone, 50); return () => window.clearTimeout(t); }
    const t = window.setInterval(() => setN((x) => x + 1), 320);
    return () => window.clearInterval(t);
  }, [onDone]);
  useEffect(() => { if (n === BUILD_STEPS.length + 2) onDone(); }, [n, onDone]);
  return (
    <motion.div className="dp-build" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, scale: 0.98, transition: { duration: 0.3 } }}>
      <div className="dp-guide lg"><Mark size={40} /></div>
      <h2>Building your blueprint</h2>
      <p className="lead">{plan.topology} · {plan.totals.vcpu} vCPU · {gb(plan.totals.storage)}</p>
      <ul>
        {BUILD_STEPS.map((s, i) => (
          <motion.li key={s} initial={{ opacity: 0, x: -10 }} animate={{ opacity: i <= n ? 1 : 0.25, x: 0 }} transition={{ duration: 0.4, ease: EASE, delay: i * 0.04 }}>
            <span className={`dot ${i < n ? "ok" : i === n ? "run" : ""}`}>{i < n && <Check />}</span>{s}
          </motion.li>
        ))}
      </ul>
    </motion.div>
  );
}

/** Reads a shared plan from the URL hash (#plan=...). */
export function useSharedAnswers(): Partial<Answers> | null | undefined {
  const [v, setV] = useState<Partial<Answers> | null | undefined>(undefined);
  useEffect(() => {
    const m = location.hash.match(/plan=([A-Za-z0-9_-]+)/);
    const decoded = m ? decodeAnswers(m[1]) : null;
    const t = window.setTimeout(() => setV(decoded), 0);
    return () => window.clearTimeout(t);
  }, []);
  return v;
}
