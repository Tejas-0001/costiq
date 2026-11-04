"use client";
// The moving parts of the blueprint's delivery and defense sections: a change running through the quality gates, one
// signed build promoted through the environments, traffic moving between pools on the load balancer, the hops a release
// takes across the client's boundary, the development plan, and attacks walking through the defense layers.
import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { useEffect, useState } from "react";
import { EASE } from "@/components/motion";
import type { DefenseLayer, Plan } from "./model";
import { LANES, OUTCOME_LABEL, THREATS, type Cutover, type Lane, type Route, type Stage, type Threat } from "./release";

const Tick = () => <svg width="11" height="11" viewBox="0 0 12 12" aria-hidden><path d="M2.5 6.2 5 8.6l4.5-5" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" /></svg>;
const Cross = () => <svg width="10" height="10" viewBox="0 0 12 12" aria-hidden><path d="M3 3l6 6M9 3 3 9" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" /></svg>;

/* ------------------------------------------------------------------ quality pipeline */

type Run = { fail: boolean; at: number; done: boolean };
const STEP_MS = 700;

/** A change moving through every stage. "Try a risky change" plants a critical CVE so the scan gate stops it. */
export function Pipeline({ stages, failAt }: { stages: Stage[]; failAt: { stage: string; gate: number; message: string } }) {
  const [run, setRun] = useState<Run | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const failIdx = stages.findIndex((s) => s.id === failAt.stage);

  useEffect(() => {
    if (!run || run.done) return;
    const t = setTimeout(() => {
      setRun((r) => {
        if (!r) return r;
        if (r.fail && r.at === failIdx) return { ...r, done: true };
        if (r.at >= stages.length - 1) return { ...r, at: stages.length, done: true };
        return { ...r, at: r.at + 1 };
      });
    }, STEP_MS);
    return () => clearTimeout(t);
  }, [run, failIdx, stages.length]);

  const start = (fail: boolean) => { setRun({ fail, at: 0, done: false }); setOpen(null); };
  // the list follows the run while it is moving and rests on where it ended (the blocking stage, or the last one);
  // after that the reader can click any stage
  const shown = run && !run.done ? Math.min(run.at, stages.length - 1) : open ?? (run?.fail ? failIdx : run ? stages.length - 1 : 0);
  const state = (i: number) => {
    if (!run) return "idle";
    if (run.fail && i === failIdx && run.at === failIdx) return run.done ? "fail" : "run";
    if (i < run.at) return "pass";
    if (i === run.at && !run.done) return "run";
    return run.fail && i > failIdx ? "skip" : "idle";
  };
  const passed = run?.done && !run.fail;
  const blocked = run?.done && run.fail;
  const stage = stages[shown];

  return (
    <motion.div className="dl-pipe dp-card" onViewportEnter={() => { if (!run) start(false); }} viewport={{ once: true, amount: 0.5 }}>
      <div className="dl-pipe-bar">
        <div>
          <b>Every change, every time</b>
          <span>{stages.reduce((n, s) => n + s.gates.length, 0)} automated gates. Nothing is promoted by hand.</span>
        </div>
        <div className="dl-pipe-btns">
          <button type="button" className="dp-btn" onClick={() => start(false)}>Run a change</button>
          <button type="button" className="dp-btn risky" onClick={() => start(true)}>Try a risky change</button>
        </div>
      </div>

      <ol className="dl-stages">
        {stages.map((s, i) => {
          const st = state(i);
          return (
            <li key={s.id} className={`${st}${shown === i ? " open" : ""}`}>
              {i > 0 && <span className="rail"><motion.i initial={false} animate={{ scaleX: st === "pass" || st === "run" || st === "fail" ? 1 : 0 }} transition={{ duration: 0.45, ease: EASE }} /></span>}
              <button type="button" onClick={() => setOpen(i)} aria-pressed={shown === i}>
                <span className="node">{st === "pass" ? <Tick /> : st === "fail" ? <Cross /> : st === "run" ? <i className="spin" /> : i + 1}</span>
                <b>{s.name}</b>
                <small>{s.where}</small>
              </button>
            </li>
          );
        })}
      </ol>

      <AnimatePresence mode="wait" initial={false}>
        <motion.div key={stage.id} className="dl-gates" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.28, ease: EASE }}>
          <table>
            <thead><tr><th>{stage.name}: gate</th><th>Tool</th><th>Must be true to pass</th><th>Satisfies</th></tr></thead>
            <tbody>
              {stage.gates.map((g, j) => {
                const st = state(shown);
                const failed = st === "fail" && j === failAt.gate;
                const ok = st === "pass" || (st === "fail" && j < failAt.gate);
                return (
                  <motion.tr key={g.name} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.3, ease: EASE, delay: j * 0.05 }} className={failed ? "failed" : ""}>
                    <td><span className={`g ${failed ? "fail" : ok ? "pass" : st === "run" ? "run" : ""}`}>{failed ? <Cross /> : ok ? <Tick /> : null}</span><b>{g.name}</b>
                      {failed && <div className="why">{failAt.message}</div>}</td>
                    <td className="tool">{g.tool}</td>
                    <td className="muted">{g.rule}</td>
                    <td><span className="std">{g.std}</span></td>
                  </motion.tr>
                );
              })}
            </tbody>
          </table>
        </motion.div>
      </AnimatePresence>

      <AnimatePresence>
        {(passed || blocked) && (
          <motion.div className={`dl-verdict ${passed ? "ok" : "bad"}`} initial={{ opacity: 0, y: 10, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.4, ease: EASE }}>
            <span className="ic">{passed ? <Tick /> : <Cross />}</span>
            {passed
              ? <p><b>Release candidate ready for test.</b> Signed image, SBOM and build provenance attached. Test receives exactly these bytes, identified by digest.</p>
              : <p><b>Blocked at {stages[failIdx].name.toLowerCase()}.</b> The change never reaches test. The developer fixes it, and the whole run starts again; no gate can be skipped or waived in the pipeline.</p>}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

/* ------------------------------------------------------------------ environments */

/** One signed build moves right; the digest never changes. Production data never moves left. */
export function Lanes({ envs, digest, data }: { envs: Lane["id"][]; digest: string; data: { label: string; tone: string } }) {
  const lanes = LANES.filter((l) => envs.includes(l.id));
  const [at, setAt] = useState(0);
  const next = lanes[at + 1];
  return (
    <div className="dl-lanes dp-card">
      <LayoutGroup>
        <div className="dl-lane-grid" style={{ gridTemplateColumns: `repeat(${lanes.length}, minmax(0, 1fr))` }}>
          {lanes.map((e, i) => (
            <div key={e.id} className={`dl-lane ${e.id}${i <= at ? " live" : ""}`}>
              <div className="hd"><b>{e.name}</b><span>{e.who}</span></div>
              <div className="slot">
                {i === at ? (
                  <motion.div layoutId="artifact" className="dl-artifact" transition={{ type: "spring", stiffness: 260, damping: 30 }}>
                    <span className="seal"><Tick /></span>
                    <div><b>costiq 1.4.0</b><code>{digest}</code></div>
                  </motion.div>
                ) : i < at ? (
                  <div className="dl-artifact ghost"><span className="seal"><Tick /></span><div><b>costiq 1.4.0</b><code>{digest}</code></div></div>
                ) : <div className="dl-artifact empty">Waiting for promotion</div>}
              </div>
              <dl>
                <dt>Data</dt><dd>{e.id === "prod" ? <span className="dl-data real">Production, stays here</span> : <span className={`dl-data ${data.tone}`}>{data.label}</span>}</dd>
                <dt>Who can change it</dt><dd>{e.access}</dd>
                <dt>To get here</dt><dd>{e.promote}</dd>
              </dl>
            </div>
          ))}
        </div>
      </LayoutGroup>
      <div className="dl-lane-foot">
        <p><b>Same bytes in every environment.</b> Each environment runs the image that passed the gates, verified by signature before it starts. Nothing is rebuilt between stages.</p>
        {next ? <button type="button" className="dp-btn primary" onClick={() => setAt(at + 1)}>Promote to {next.name.toLowerCase()}<span className="enter">→</span></button>
          : lanes.length > 1 ? <button type="button" className="dp-btn" onClick={() => setAt(0)}>Start again</button> : null}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ cutover on the load balancer */

/** Traffic moving from the live version to the new one, step by step, as the chosen strategy would do it. */
export function TrafficSplit({ c, via, f5 }: { c: Cutover; via: string; f5: boolean }) {
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  useEffect(() => {
    if (!playing) return;
    if (step >= c.steps.length - 1) { const t = setTimeout(() => setPlaying(false), 0); return () => clearTimeout(t); }
    const t = setTimeout(() => setStep((s) => s + 1), 1500);
    return () => clearTimeout(t);
  }, [playing, step, c.steps.length]);
  const s = c.steps[Math.min(step, c.steps.length - 1)];
  return (
    <div className="dl-split">
      <div className={`dl-split-viz${f5 ? " f5" : ""}`}>
        <div className="users">Your users</div>
        <div className="arrow" />
        <div className="lbx"><b>{via}</b><span>{c.mechanism(f5)}</span></div>
        <div className="pools">
          {([["blue", c.from, s.live], ["green", c.to, s.next]] as const).map(([cls, label, pct]) => (
            <div key={cls} className={`pool ${cls}`}>
              <div className="hd"><b>{label}</b><motion.span key={`${step}${cls}`} initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}>{pct}%</motion.span></div>
              <div className="bar"><motion.i initial={false} animate={{ width: `${pct}%` }} transition={{ duration: 0.8, ease: EASE }} /></div>
            </div>
          ))}
        </div>
      </div>
      <ol className="dl-split-steps">
        {c.steps.map((x, i) => (
          <li key={x.label} className={i < step ? "done" : i === step ? "on" : ""}>
            <button type="button" onClick={() => { setPlaying(false); setStep(i); }}>
              <i>{i < step ? <Tick /> : i + 1}</i><b>{x.label}</b><span>{x.check}</span>
            </button>
          </li>
        ))}
      </ol>
      <div className="dl-split-foot">
        <button type="button" className="dp-btn" onClick={() => { setStep(0); setPlaying(true); }} disabled={playing}>{playing ? "Moving traffic…" : step ? "Replay" : "Play the cutover"}</button>
        <span>Rollback: {c.rollback(f5)}</span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ route path across the boundary */

/** The hops a release takes on a route. "|" in the flow marks where it crosses into the client's boundary. */
export function RouteFlow({ r }: { r: Route }) {
  const cut = r.flow.indexOf("|");
  const outside = cut < 0 ? [] : r.flow.slice(0, cut);
  const inside = cut < 0 ? r.flow : r.flow.slice(cut + 1);
  // each arrow travels with the hop it points to, so a wrapped line never ends on a dangling arrow
  const hop = (label: string, i: number, side: string) => (
    <motion.span key={label} className="hop-wrap" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35, ease: EASE, delay: i * 0.06 }}>
      {i > 0 && <span className="hop-arrow" />}
      <span className={`hop ${side}`}>{label}</span>
    </motion.span>
  );
  return (
    <div className="dl-flow">
      {outside.length > 0 && <div className="zone out"><span className="zl">Our side: code only, never your data</span><div className="hops">{outside.map((h, i) => hop(h, i, "out"))}</div></div>}
      {outside.length > 0 && <div className="wall"><span>Your boundary</span></div>}
      <div className="zone in"><span className="zl">{outside.length ? "Inside your boundary" : "Entirely inside your boundary"}</span><div className="hops">{inside.map((h, i) => hop(h, i + outside.length, "in"))}</div></div>
    </div>
  );
}

/* ------------------------------------------------------------------ development plan */

export function DevPlan({ timeline }: { timeline: Plan["timeline"] }) {
  const [on, setOn] = useState(0);
  const max = Math.max(...timeline.map((p) => p.start + p.weeks));
  const p = timeline[on];
  const wk = (n: number) => Math.round(n) + 1;
  return (
    <div className="dp-card dl-plan">
      <div className="dl-gantt">
        {timeline.map((x, i) => (
          <button key={x.phase} type="button" className={`row${i === on ? " on" : ""}`} onClick={() => setOn(i)} aria-pressed={i === on}>
            <span className="lbl"><b>{x.phase}</b><span>{x.weeks} weeks</span></span>
            <span className="track">
              <motion.i className={`p${i % 6}`} initial={{ scaleX: 0 }} whileInView={{ scaleX: 1 }} viewport={{ once: true }}
                transition={{ duration: 0.7, ease: EASE, delay: 0.1 + i * 0.1 }} style={{ left: `${(x.start / max) * 100}%`, width: `${(x.weeks / max) * 100}%` }} />
              <motion.em className="gate" initial={{ scale: 0 }} whileInView={{ scale: 1 }} viewport={{ once: true }}
                transition={{ duration: 0.35, ease: EASE, delay: 0.6 + i * 0.1 }} style={{ left: `${((x.start + x.weeks) / max) * 100}%` }} title={`Gate ${i + 1}`} />
            </span>
          </button>
        ))}
        <div className="axis"><span>Week 1</span><span>Week {Math.ceil(max)}</span></div>
      </div>
      <AnimatePresence mode="wait" initial={false}>
        <motion.div key={p.phase} className="dl-phase" initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -8 }} transition={{ duration: 0.3, ease: EASE }}>
          <span className="k">Phase {on + 1} · weeks {wk(p.start)}–{wk(p.start + p.weeks - 1)}</span>
          <h4>{p.phase}</h4>
          <p>{p.detail}</p>
          <div className="cols">
            <div><span className="h">What happens</span><ul>{p.work.map((w) => <li key={w}>{w}</li>)}</ul></div>
            <div><span className="h gate">Exit gate {on + 1}: all must be true</span><ul className="checks">{p.exit.map((w) => <li key={w}>{w}</li>)}</ul></div>
          </div>
          <p className="sign"><b>Signed by</b> {p.signoff}</p>
        </motion.div>
      </AnimatePresence>
    </div>
  );
}

/* ------------------------------------------------------------------ attacks against the defense layers */

/** Pick an attack and watch it meet each layer in turn: blocked, contained, detected or recovered, and by whose tool. */
export function DefenseSim({ layers, threats = THREATS }: { layers: DefenseLayer[]; threats?: Threat[] }) {
  const [id, setId] = useState(threats[0].id);
  const [at, setAt] = useState(-2); // -2: waiting to be scrolled into view
  const threat = threats.find((t) => t.id === id)!;
  useEffect(() => {
    if (at < -1 || at >= threat.steps.length) return;
    const t = setTimeout(() => setAt((x) => x + 1), at < 0 ? 350 : 900);
    return () => clearTimeout(t);
  }, [at, threat.steps.length]);
  const pick = (t: string) => { setId(t); setAt(-1); };
  const layer = (lid: string) => layers.find((l) => l.id === lid)!;
  // where the attack stops: the first layer that blocks it, else the first that contains it
  const stopAt = Math.max(0, threat.steps.findIndex((s) => s.outcome === "block"), threat.steps.findIndex((s) => s.outcome === "block") < 0 ? threat.steps.findIndex((s) => s.outcome === "contain") : -1);
  const stopped = threat.steps[stopAt];
  const behind = threat.steps.length - 1 - stopAt;
  return (
    <motion.div className="dp-card dl-sim" onViewportEnter={() => { if (at === -2) setAt(-1); }} viewport={{ once: true, amount: 0.4 }}>
      <div className="dl-sim-tabs" role="tablist" aria-label="Attacks">
        {threats.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={t.id === id} className={t.id === id ? "on" : ""} onClick={() => pick(t.id)}>
            {t.id === id && <motion.span layoutId="dl-sim-hl" className="hl" transition={{ type: "spring", stiffness: 420, damping: 34 }} />}
            <span>{t.name}</span>
          </button>
        ))}
      </div>
      <AnimatePresence mode="wait" initial={false}>
        <motion.div key={id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.3, ease: EASE }}>
          <div className="dl-sim-hd">
            <span className="attacker" aria-hidden>!</span>
            <div><b>{threat.attack}</b><a href={threat.url} target="_blank" rel="noopener noreferrer">MITRE ATT&amp;CK {threat.technique}</a></div>
          </div>
          <ol className="dl-sim-steps">
            {threat.steps.map((s, i) => {
              const l = layer(s.layer);
              const reached = i < at;
              return (
                <li key={i} className={`${reached ? `hit ${s.outcome}` : i === at ? "now" : ""}`}>
                  <span className="rail" />
                  <span className="mark">{reached ? (s.outcome === "detect" ? "◉" : s.outcome === "recover" ? "↺" : <Tick />) : i + 1}</span>
                  <div className="body">
                    <div className="top"><b>{l.label}</b><span className={`own ${l.owner}`}>{l.owner === "yours" ? "Yours" : "We bring"} · {l.product}</span>
                      <AnimatePresence>{reached && <motion.em className={`out ${s.outcome}`} initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }} transition={{ duration: 0.25, ease: EASE }}>{OUTCOME_LABEL[s.outcome]}</motion.em>}</AnimatePresence>
                    </div>
                    <p>{s.text}</p>
                  </div>
                </li>
              );
            })}
          </ol>
          <AnimatePresence>
            {at >= threat.steps.length && stopped && (
              <motion.p className="dl-sim-verdict" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35, ease: EASE }}>
                <span><b>{stopped.outcome === "block" ? "Stopped" : "Contained"} by {layer(stopped.layer).product}</b>{behind > 0 ? `, with ${behind} more layer${behind > 1 ? "s" : ""} behind it.` : "."}</span>
                <button type="button" onClick={() => setAt(-1)}>Replay</button>
              </motion.p>
            )}
          </AnimatePresence>
        </motion.div>
      </AnimatePresence>
    </motion.div>
  );
}
