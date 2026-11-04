"use client";
// CostIQ Agent: plans the steps for a question, runs CostIQ's API as the signed-in role and shows its working, so the
// numbers, timings and permission refusals are genuine. "Brief me" uses the AI summary, whose figures link to their pages.
// Open with the launcher or Ctrl/⌘+K.
import Link from "next/link";
import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useCopy } from "@/components/copy";
import { EASE } from "@/components/motion";
import { useMe } from "@/components/shell";
import { Linked, SummaryBody, type Fact, type Summary } from "@/components/summary";
import { api, post } from "@/lib/api";
import { compact } from "@/lib/format";

type Step = { label: string; tool?: string; state: "run" | "done" | "fail"; ms?: number };
type Bar = { label: string; value: number; tone?: string; text?: string };
type Viz = { kind: "bars"; title: string; rows: Bar[] } | { kind: "cols"; title: string; rows: Bar[] } | { kind: "table"; title: string; columns: string[]; rows: unknown[][] }
  | { kind: "cards"; rows: { label: string; value: string; tone: string; href: string }[] } | { kind: "summary"; data: Summary };
type Turn = {
  id: number; q: string; steps: Step[]; answer: string; shown: number; done: boolean; facts?: Record<string, Fact>;
  viz?: Viz[]; actions?: { label: string; href: string }[]; follow?: string[];
};

const PERIOD_WORDS: [RegExp, string, string][] = [
  [/today/, "today", "today"], [/yesterday/, "yesterday", "yesterday"], [/week|wtd/, "wtd", "week to date"],
  [/year|ytd/, "ytd", "year to date"], [/./, "mtd", "month to date"],
];
const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);
const pause = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** The tenant's words for the scripted flows (copy agent.*): the risk and unlock questions, step labels, answers and
 *  reason labels. The flows and their API calls are the same for every tenant. */
type Words = { t: (key: string, vars?: Record<string, string | number>) => string; get: <T>(key: string) => T | undefined };
const suggestions = (w: Words) => ["Brief me on this month", w.t("agent.questions.risk"), "How much did we spend today?",
  "Where can we save the most?", w.t("agent.questions.credit"), "Any alerts I should know about?"];
const reason = (w: Words, code: string | undefined) => (code ? w.get<Record<string, string>>("agent.reasons")?.[code] : undefined);

// ------------------------------------------------------------------------------------------------ the agent's tools
type Run = {
  period: string;
  w: Words;
  step: (label: string, tool?: string) => Promise<void>;
  call: <T>(label: string, tool: string, fn: () => Promise<T>) => Promise<T | null>;
  say: (text: string, facts?: Record<string, Fact>) => void;
  show: (v: Viz) => void;
  act: (a: { label: string; href: string }[], follow?: string[]) => void;
};

async function spend(r: Run, q: string) {
  const named = PERIOD_WORDS.slice(0, -1).find(([re]) => re.test(q));
  const sel = PERIOD_WORDS.find(([, k]) => k === r.period) ?? PERIOD_WORDS[PERIOD_WORDS.length - 1];
  const [, range, long] = named ?? sel;  // a period named in the question, else the one selected at the top of the page
  await r.step(`Reading the question: spend, ${long}`);
  const d = await r.call("Pulling spend for the period", `GET /api/period?range=${range}`, () => api<{
    total: number; cloud: number; ai: number; change_pct: number | null; start: string; end: string;
    daily: { Day: string; Cloud: number; AI: number; InPeriod: boolean }[]; providers: { Provider: string; Cost: number }[];
  }>(`/period?range=${range}`));
  if (!d) return;
  await r.step("Comparing with the prior period");
  const ch = d.change_pct == null ? "There's no earlier period in the data to compare with." :
    `That's **${d.change_pct < 0 ? "down" : "up"} ${Math.abs(d.change_pct).toFixed(1)}%** on the comparable prior period.`;
  r.say(`We spent **${compact(d.total)}** ${long} (${d.start === d.end ? d.start : `${d.start} to ${d.end}`}). ${ch} ` +
    `Cloud and SaaS is ${Math.round((100 * d.cloud) / Math.max(1, d.total))}% of it and AI ${Math.round((100 * d.ai) / Math.max(1, d.total))}%. ` +
    `**${d.providers[0]?.Provider}** is the largest provider at ${compact(d.providers[0]?.Cost)}.`);
  r.show({ kind: "bars", title: "Top providers", rows: d.providers.slice(0, 5).map((p) => ({ label: p.Provider, value: p.Cost })) });
  r.act([{ label: "Open the Command Center", href: "/command" }], ["Where can we save the most?", "How much did we spend this year?"]);
}

async function risk(r: Run) {
  const w = r.w;
  await r.step(w.t("agent.risk.step"));
  const d = await r.call(w.t("agent.risk.call"), "GET /api/risk", () => api<{
    deduction_at_risk: number; evidence_score: number; lines_at_risk: number; lines: number;
    by_reason: { RiskReason: string; Risk: number }[]; top: { Item: string; Vendor: string; FilingId: string; Risk: number; RiskReason: string; Fix: { kind: string; target?: string; label: string } }[];
  }>("/risk"));
  if (!d) return;
  await r.step("Ranking the largest risks and their fixes");
  const top = d.top.slice(0, 3);
  r.say(w.t("agent.risk.answer", {
    amount: compact(d.deduction_at_risk), score: d.evidence_score, atRisk: d.lines_at_risk.toLocaleString("en-US"),
    lines: d.lines.toLocaleString("en-US"), reason: reason(w, d.by_reason[0]?.RiskReason) ?? "evidence gaps",
    top: top.map((t) => `**${t.Item}** (${t.Vendor}, ${compact(t.Risk)} in ${t.FilingId})`).join(", "),
  }));
  r.show({ kind: "bars", title: "Why it's at risk", rows: d.by_reason.map((x) => ({ label: cap(reason(w, x.RiskReason) ?? x.RiskReason), value: x.Risk, tone: "risk" })) });
  const fix = top.find((t) => t.Fix.kind === "optimization" && t.Fix.target);
  r.act([{ label: `Open the ${w.t("nav.items.risk")}`, href: "/risk?sec=Largest%20risks" }, ...(fix ? [{ label: `Open fix ${fix.Fix.target}`, href: `/optimization?focus=${fix.Fix.target}` }] : [])],
    ["Where can we save the most?", w.t("agent.questions.credit")]);
}

async function savings(r: Run) {
  await r.step("Reading the question: savings opportunities");
  const d = await r.call("Loading the savings tracker", "GET /api/optimization", () => api<{
    stages: { Stage: string; MonthlySavings: number; Actions: number }[]; verified_annual: number;
    by_lever: { LeverLabel: string; Identified: number; Actions: number }[];
  }>("/optimization"));
  if (!d) return;
  await r.step("Ranking levers by monthly savings");
  const id = d.stages[0], ap = d.stages[1];
  const lev = [...d.by_lever].sort((a, b) => b.Identified - a.Identified).slice(0, 3);
  r.say(`There's **${compact(id.MonthlySavings)}/month** identified across ${id.Actions} actions, of which ${compact(ap.MonthlySavings)}/month is approved. ` +
    `Verified savings so far: **${compact(d.verified_annual)}/yr**. The biggest levers are ${lev.map((l) => `**${l.LeverLabel}** (${compact(l.Identified)}/mo)`).join(", ")}.`);
  r.show({ kind: "bars", title: "Savings a month, by lever", rows: lev.map((l) => ({ label: l.LeverLabel, value: l.Identified, tone: "save" })) });
  r.act([{ label: "Open the Optimization Center", href: "/optimization?sec=Backlog" }], [r.w.t("agent.questions.risk"), "Any alerts I should know about?"]);
}

async function credit(r: Run) {
  const w = r.w;
  await r.step(w.t("agent.credit.step"));
  const d = await r.call(w.t("agent.credit.call"), "GET /api/tax/credit-unlock", () => api<{
    credit_unlock: number; credit_claimed: number; jurisdictions: { Jurisdiction: string; Credit: number; CreditStatus: string }[];
  }>("/tax/credit-unlock"));
  if (!d) return;
  await r.step(w.t("agent.credit.split"));
  const best = [...d.jurisdictions].filter((j) => j.CreditStatus === "eligible").sort((a, b) => b.Credit - a.Credit)[0];
  r.say(w.t("agent.credit.answer", { unlock: compact(d.credit_unlock), claimed: compact(d.credit_claimed) }) +
    (best ? w.t("agent.credit.best", { jurisdiction: best.Jurisdiction, amount: compact(best.Credit) }) : ""));
  r.show({ kind: "cols", title: w.t("agent.credit.chart"), rows: d.jurisdictions.map((j) => ({ label: j.Jurisdiction, value: j.Credit, tone: j.CreditStatus === "claimed" ? "earned" : "whatif" })) });
  r.act([{ label: "Model it by jurisdiction", href: best ? `/tax?tab=credit&hl=${best.Jurisdiction}` : "/tax?tab=credit" }], [w.t("agent.questions.risk")]);
}

async function alerts(r: Run) {
  await r.step("Reading the question: alerts and anomalies");
  const d = await r.call("Checking fired alerts", "GET /api/alerts", () => api<{ unacknowledged: number; events: { Severity: string; Message: string; Acknowledged: boolean }[] }>("/alerts"));
  if (!d) return;
  const open = d.events.filter((e) => !e.Acknowledged);
  const high = open.filter((e) => e.Severity === "high");
  await r.step("Picking what needs attention");
  r.say(`You have **${d.unacknowledged} unacknowledged alerts**, ${high.length} of them high severity. ` +
    (high.length ? `Most urgent: ${high.slice(0, 2).map((e) => `**${e.Message.split(" vs ")[0]}**`).join("; ")}.` : "Nothing high severity right now."));
  r.show({ kind: "table", title: "Open alerts", columns: ["Severity", "Message"], rows: open.slice(0, 4).map((e) => [e.Severity, e.Message]) });
  r.act([{ label: "Open alerts", href: high[0] ? `/alerts?hl=${encodeURIComponent(high[0].Message)}` : "/alerts" }], ["Brief me on this month"]);
}

async function brief(r: Run) {
  await r.step(r.w.t("agent.brief.step"));
  const d = await r.call("Reading the figures behind every page you can open", `GET /api/summary?range=${r.period}`, () => api<Summary>(`/summary?range=${r.period}`));
  if (!d) return;
  await r.step(`Linking ${Object.keys(d.facts).length} figures to ${d.sources.length} source pages`);
  await r.step("Writing the briefing");
  r.say(d.headline, d.facts);
  r.show({ kind: "summary", data: d });
  r.act([{ label: "Open the CostIQ Summary", href: "/summary" }], [r.w.t("agent.questions.risk"), "Where can we save the most?", "How much did we spend today?"]);
}

async function ask(r: Run, q: string) {
  await r.step("Matching the question to CostIQ's tested queries");
  const d = await r.call("Running the best-matching use case", "POST /api/ask", () => post<{
    status: string; message?: string; headline?: string; use_case?: { id: string; title: string };
    result?: { columns: string[]; rows: unknown[][] }; suggestions?: { id: string; title: string }[];
  }>("/ask", { question: q, range: r.period }));
  if (!d) return;
  if (d.status !== "answered" || !d.result) {
    r.say(`I couldn't match that to a tested query. ${d.message ?? ""} Try one of these, or ask about ${r.w.t("agent.topics")}.`);
    r.act((d.suggestions ?? []).slice(0, 3).map((s) => ({ label: `${s.id} · ${s.title}`, href: `/catalog/${s.id}` })), suggestions(r.w).slice(0, 3));
    return;
  }
  await r.step(`Reading ${d.result.rows.length} rows from ${d.use_case?.id}`);
  r.say(`Using **${d.use_case?.id} · ${d.use_case?.title}**: ${d.headline}`);
  const cols = d.result.columns.slice(0, 3);
  r.show({ kind: "table", title: d.use_case?.title ?? "Result", columns: cols, rows: d.result.rows.slice(0, 5).map((row) => row.slice(0, 3)) });
  r.act([{ label: "Open the full query and evidence", href: `/catalog/${d.use_case?.id}` }], suggestions(r.w).slice(0, 2));
}

/** The tenant's words that send a question to the risk or unlock flow (copy agent.route.*); never matches when unset. */
const routeWords = (w: Words, key: string) => { const p = w.t(`agent.route.${key}`); return p ? new RegExp(p) : /$^/; };

function route(q: string, w: Words): (r: Run, q: string) => Promise<void> {
  const s = q.toLowerCase();
  if (/brief|summar|overview|what should i|catch me up/.test(s)) return brief;
  if (/alert|anomal|spike|warning/.test(s)) return alerts;
  if (routeWords(w, "credit").test(s)) return credit;
  if (routeWords(w, "risk").test(s)) return risk;
  if (/sav|optimi|cut|reduce|waste|lever/.test(s)) return savings;
  if (/spen|cost|bill|how much/.test(s)) return spend;
  return ask;
}

// ------------------------------------------------------------------------------------------------ rendering
function VizView({ v }: { v: Viz }) {
  if (v.kind === "summary") return <div className="ag-viz ag-sum"><SummaryBody d={v.data} compact /></div>;
  if (v.kind === "cards") {
    return (
      <div className="ag-cards">
        {v.rows.map((c, i) => (
          <motion.div key={c.label} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4, ease: EASE, delay: i * 0.06 }}>
            <Link href={c.href} className={`ag-card ${c.tone}`}><span>{c.label}</span><b>{c.value}</b></Link>
          </motion.div>
        ))}
      </div>
    );
  }
  if (v.kind === "table") {
    return (
      <div className="ag-viz">
        <span className="ag-viz-t">{v.title}</span>
        <table className="ag-table"><thead><tr>{v.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>{v.rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{typeof c === "number" ? (Math.abs(c) >= 100 ? compact(c) : c.toLocaleString("en-US")) : String(c ?? "—")}</td>)}</tr>)}</tbody></table>
      </div>
    );
  }
  const max = Math.max(1, ...v.rows.map((r) => r.value));
  if (v.kind === "cols") {
    return (
      <div className="ag-viz">
        <span className="ag-viz-t">{v.title}</span>
        <div className="ag-cols">
          {v.rows.map((r, i) => (
            <div key={r.label} className="c"><span className="v">{compact(r.value)}</span>
              <span className="b"><motion.i className={r.tone} initial={{ height: 0 }} animate={{ height: `${(r.value / max) * 100}%` }} transition={{ duration: 0.8, ease: EASE, delay: i * 0.06 }} /></span>
              <span className="l">{r.label}</span></div>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div className="ag-viz">
      <span className="ag-viz-t">{v.title}</span>
      {v.rows.map((r, i) => (
        <div key={r.label} className="hrow">
          <span className="lbl">{r.label}</span>
          <span className="track"><motion.i className={r.tone} initial={{ width: 0 }} animate={{ width: `${(r.value / max) * 100}%` }} transition={{ duration: 0.8, ease: EASE, delay: i * 0.06 }} /></span>
          <span className="amt">{compact(r.value)}</span>
        </div>
      ))}
    </div>
  );
}

const Avatar = ({ big = false }: { big?: boolean }) => (
  <span className={`ag-av${big ? " big" : ""}`} aria-hidden>
    <svg width={big ? 16 : 14} height={big ? 16 : 14} viewBox="0 0 32 32"><path d="M6.5 17.5h4.2l2.6-6 4 10.5 3-6.3 1.6 2.6 4-6.3" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" /></svg>
  </span>
);

function TurnView({ t, onAsk }: { t: Turn; onAsk: (q: string) => void }) {
  const words = t.answer.split(" ");
  const text = words.slice(0, t.shown).join(" ");
  return (
    <div className="ag-turn">
      <motion.div className="ag-q" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35, ease: EASE }}>{t.q}</motion.div>
      <div className="ag-steps">
        <AnimatePresence initial={false}>
          {t.steps.map((s, i) => (
            <motion.div key={i} className={`ag-step ${s.state}`} initial={{ opacity: 0, x: -6, height: 0 }} animate={{ opacity: 1, x: 0, height: "auto" }} transition={{ duration: 0.3, ease: EASE }}>
              <span className="ic">{s.state === "run" ? <i className="spin" /> : s.state === "done" ? "✓" : "!"}</span>
              <span className="lb">{s.label}</span>
              {s.tool && <code>{s.tool}{s.ms != null ? ` · ${s.ms} ms` : ""}</code>}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
      {t.answer && (
        <div className="ag-a">
          <Avatar />
          <div className="ag-a-body">
            <p><Linked text={text} facts={t.facts ?? {}} />{!t.done && <span className="caret" />}</p>
            {t.done && t.viz?.map((v, i) => <VizView key={i} v={v} />)}
            {t.done && !!t.actions?.length && (
              <div className="ag-acts">{t.actions.map((a) => <Link key={a.href} href={a.href} className="ag-act">{a.label} →</Link>)}</div>
            )}
            {t.done && !!t.follow?.length && (
              <div className="ag-follow">{t.follow.map((f) => <button key={f} type="button" onClick={() => onAsk(f)}>{f}</button>)}</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function Agent() {
  const { me, period } = useMe();
  const words: Words = useCopy();
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen((o) => !o); }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => { if (open) setTimeout(() => input.current?.focus(), 250); }, [open]);
  useEffect(() => { scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" }); }, [turns]);

  const run = useCallback(async (question: string) => {
    if (!question.trim() || busy) return;
    setBusy(true); setQ("");
    const id = Date.now();
    const patch = (f: (t: Turn) => Turn) => setTurns((ts) => ts.map((t) => (t.id === id ? f(t) : t)));
    setTurns((ts) => [...ts, { id, q: question, steps: [], answer: "", shown: 0, done: false }]);
    const addStep = (s: Step) => patch((t) => ({ ...t, steps: [...t.steps, s] }));
    const finishLast = (st: Step["state"], ms?: number) => patch((t) => ({ ...t, steps: t.steps.map((s, i) => (i === t.steps.length - 1 ? { ...s, state: st, ms: ms ?? s.ms } : s)) }));
    let answer = "";
    const r: Run = {
      period,
      w: words,
      step: async (label, tool) => { addStep({ label, tool, state: "run" }); await pause(420); finishLast("done"); },
      call: async (label, tool, fn) => {
        addStep({ label, tool, state: "run" });
        // the chip shows the real API time; the short pause only keeps each step on screen long enough to read
        const t0 = performance.now();
        let ms = 0;
        const timed = fn().finally(() => { ms = Math.round(performance.now() - t0); });
        try {
          const [v] = await Promise.all([timed, pause(520)]);
          finishLast("done", ms);
          return v;
        } catch (e) {
          await pause(Math.max(0, 520 - (performance.now() - t0)));
          finishLast("fail", ms);
          const err = e as Error & { status?: number };
          answer = err.status === 403 ? `As ${me.personas[0]?.label.replace(/\s*\((.*)\)/, ", $1")}, you can't open that data, so I've left it out.` : `That call failed: ${err.message}`;
          return null;
        }
      },
      say: (text, facts) => { answer = text; if (facts) patch((t) => ({ ...t, facts })); },
      show: (v) => patch((t) => ({ ...t, viz: [...(t.viz ?? []), v] })),
      act: (actions, follow) => patch((t) => ({ ...t, actions, follow })),
    };
    try { await route(question, words)(r, question); } catch (e) { answer = `Something went wrong: ${(e as Error).message}`; }
    patch((t) => ({ ...t, answer }));
    const n = answer.split(" ").length;
    for (let i = 1; i <= n; i += 2) { await pause(22); patch((t) => ({ ...t, shown: i })); }
    patch((t) => ({ ...t, shown: n, done: true }));
    setBusy(false);
  }, [busy, me.personas, period, words]);

  return (
    <>
      <AnimatePresence>
        {!open && (
          <motion.button type="button" className="ag-launch" onClick={() => setOpen(true)} initial={{ opacity: 0, y: 16, scale: 0.9 }}
            animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 16, scale: 0.9 }} transition={{ duration: 0.35, ease: EASE }} whileTap={{ scale: 0.96 }}>
            <span className="spark" aria-hidden>✦</span>Ask the agent<kbd>Ctrl K</kbd>
          </motion.button>
        )}
      </AnimatePresence>
      <AnimatePresence>
        {open && (
          <motion.aside className="ag" role="dialog" aria-label="CostIQ Agent" initial={{ opacity: 0, x: 40, scale: 0.98 }} animate={{ opacity: 1, x: 0, scale: 1 }}
            exit={{ opacity: 0, x: 40, scale: 0.98, transition: { duration: 0.2 } }} transition={{ duration: 0.45, ease: EASE }}>
            <header className="ag-head">
              <Avatar big />
              <div><b>CostIQ Agent</b><span>Runs CostIQ&apos;s tested queries as your role</span></div>
              {turns.length > 0 && <button type="button" className="ag-x" onClick={() => setTurns([])} title="New conversation" aria-label="New conversation">
                <svg width="14" height="14" viewBox="0 0 24 24"><path d="M4 12a8 8 0 1 0 2.3-5.6M4 4v4h4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg></button>}
              <button type="button" className="ag-x" onClick={() => setOpen(false)} aria-label="Close agent">
                <svg width="12" height="12" viewBox="0 0 14 14"><path d="M2 2l10 10M12 2 2 12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg></button>
            </header>
            <div className="ag-body" ref={scroller}>
              {turns.length === 0 ? (
                <div className="ag-empty">
                  <motion.h3 initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5, ease: EASE }}>What should we look into?</motion.h3>
                  <p>Signed in as {me.personas[0]?.label}. I plan the steps, run CostIQ&apos;s queries as your role, and show my working.</p>
                  <div className="ag-sugs">
                    {suggestions(words).map((s, i) => (
                      <motion.button key={s} type="button" onClick={() => run(s)} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.4, ease: EASE, delay: 0.1 + i * 0.05 }}>{s}</motion.button>
                    ))}
                  </div>
                </div>
              ) : turns.map((t) => <TurnView key={t.id} t={t} onAsk={run} />)}
            </div>
            <form className="ag-input" onSubmit={(e) => { e.preventDefault(); run(q); }}>
              <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder={busy ? "Working…" : "Ask about spend, risk, savings, alerts…"} disabled={busy} maxLength={300} />
              <button type="submit" disabled={busy || !q.trim()} aria-label="Send">
                <svg width="16" height="16" viewBox="0 0 24 24"><path d="M5 12h13M13 6l6 6-6 6" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
              </button>
            </form>
          </motion.aside>
        )}
      </AnimatePresence>
    </>
  );
}
