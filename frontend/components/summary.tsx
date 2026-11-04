"use client";
// AI Summary page: the headline, what matters in each area, and what to do next, for the signed-in role. Every figure is a link
// to the page and row it comes from (the target page scrolls to it and highlights it); citation numbers list the sources.
import Link from "next/link";
import { motion } from "motion/react";
import { useState } from "react";
import { EASE } from "@/components/motion";
import { useMe, type PeriodInfo } from "@/components/shell";
import { api, useApi } from "@/lib/api";

export type Fact = { text: string; label: string; href: string | null; source: number };
export type Source = { n: number; page: string; section: string; href: string | null };
export type Summary = {
  as_of: string; generated_at: string; writer: "costiq" | "saved"; model: string | null; note: string | null; period: PeriodInfo;
  headline: string;
  sections: { key: string; title: string; tone: "good" | "warn" | "bad" | "info"; state: string; sentences: string[] }[];
  actions: { kind: string; title: string; detail: string; value: string; href: string }[];
  facts: Record<string, Fact>; sources: Source[];
};

const TOKEN = /(⟦f\d+⟧|\*\*[^*]+\*\*)/;

/** Text with ⟦fN⟧ citations rendered as linked figures. `hot` highlights the figures from one source. */
export function Linked({ text, facts, hot, onHot }: { text: string; facts: Record<string, Fact>; hot?: number | null; onHot?: (n: number | null) => void }) {
  return (
    <>
      {text.split(TOKEN).map((p, i) => {
        const m = /^⟦(f\d+)⟧$/.exec(p);
        if (m) {
          const f = facts[m[1]];
          if (!f) return null;
          const cls = `fact${hot === f.source ? " hot" : ""}`;
          const tip = `${f.label} · source ${f.source}`;
          return f.href
            ? <Link key={i} href={f.href} className={cls} title={tip} onMouseEnter={() => onHot?.(f.source)} onMouseLeave={() => onHot?.(null)}>{f.text}</Link>
            : <b key={i} className={cls} title={tip}>{f.text}</b>;
        }
        if (p.startsWith("**")) return <b key={i}>{p.slice(2, -2)}</b>;
        return <span key={i}>{p}</span>;
      })}
    </>
  );
}

/** The source numbers a sentence cites, as small linked chips. */
function Cites({ text, data, hot, onHot }: { text: string; data: Summary; hot: number | null; onHot: (n: number | null) => void }) {
  const ns = [...new Set([...text.matchAll(/⟦(f\d+)⟧/g)].map((m) => data.facts[m[1]]?.source).filter(Boolean))] as number[];
  return (
    <span className="cites">
      {ns.map((n) => {
        const s = data.sources[n - 1];
        const body = <span className={`cite${hot === n ? " hot" : ""}`} onMouseEnter={() => onHot(n)} onMouseLeave={() => onHot(null)}>{n}</span>;
        return s?.href ? <Link key={n} href={s.href} title={`${s.page} › ${s.section}`}>{body}</Link> : <span key={n} title={s ? `${s.page} › ${s.section}` : ""}>{body}</span>;
      })}
    </span>
  );
}

/** Plain text of the summary (figures inline, sources listed), for the clipboard. */
function asText(d: Summary) {
  const t = (s: string) => s.replace(/⟦(f\d+)⟧/g, (_, id) => d.facts[id]?.text ?? "");
  const lines = [`CostIQ summary, ${d.period?.label ?? ""}, data as of ${d.as_of}`, "", t(d.headline), ""];
  d.sections.forEach((s) => { lines.push(s.title.toUpperCase()); s.sentences.forEach((x) => lines.push(`- ${t(x)}`)); lines.push(""); });
  if (d.actions.length) { lines.push("DO NEXT"); d.actions.forEach((a, i) => lines.push(`${i + 1}. ${a.title}: ${a.detail} (${a.value})`)); lines.push(""); }
  lines.push("SOURCES"); d.sources.forEach((s) => lines.push(`${s.n}. ${s.page} › ${s.section}`));
  return lines.join("\n");
}

/** The summary body: headline, sections, next actions and sources. Shared by the AI Summary page and the agent. */
export function SummaryBody({ d, compact = false }: { d: Summary; compact?: boolean }) {
  const [hot, setHot] = useState<number | null>(null);
  let k = 0;
  const rise = (): object => ({ initial: { opacity: 0, y: 6 }, animate: { opacity: 1, y: 0 }, transition: { duration: 0.45, ease: EASE, delay: 0.04 * k++ } });
  return (
    <div className={`ais-body${compact ? " compact" : ""}`}>
      {!compact && <motion.p className="ais-headline" {...rise()}><Linked text={d.headline} facts={d.facts} hot={hot} onHot={setHot} /></motion.p>}
      <div className="ais-cols">
        {d.sections.map((s) => (
          <motion.div key={s.key} className="ais-sec" {...rise()}>
            <h3>{s.title}<span className={`badge b-${s.tone}`}>{s.state}</span></h3>
            <ul>
              {s.sentences.map((t, i) => (
                <motion.li key={i} {...rise()}><Linked text={t} facts={d.facts} hot={hot} onHot={setHot} /> <Cites text={t} data={d} hot={hot} onHot={setHot} /></motion.li>
              ))}
            </ul>
          </motion.div>
        ))}
      </div>
      {d.actions.length > 0 && (
        <motion.div className="ais-next" {...rise()}>
          <h3>Do next</h3>
          <ol>
            {d.actions.map((a) => (
              <li key={a.title}>
                <Link href={a.href}><b>{a.title}</b><span>{a.detail}</span><em>{a.value}</em><i aria-hidden>→</i></Link>
              </li>
            ))}
          </ol>
        </motion.div>
      )}
      <details className="ais-src" open={!compact}>
        <summary>Sources · {d.sources.length} {d.sources.length === 1 ? "page" : "pages"}</summary>
        <ol>
          {d.sources.map((s) => (
            <li key={s.n} className={hot === s.n ? "hot" : ""} onMouseEnter={() => setHot(s.n)} onMouseLeave={() => setHot(null)}>
              <span className="cite">{s.n}</span>
              {s.href ? <Link href={s.href}>{s.page} › {s.section}</Link> : <span className="muted">{s.page} › {s.section} (not open to your role)</span>}
            </li>
          ))}
        </ol>
      </details>
    </div>
  );
}

/** The AI Summary page. */
export function SummaryView() {
  const { version, period } = useMe();
  const [fresh, setFresh] = useState<Summary | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const state = useApi<Summary>(`/summary?range=${period}`, version);
  // a regenerated summary wins until the page refetches a newer one (after a decision or a period change)
  const d = fresh && fresh.period.range === period && (!state.data || fresh.generated_at >= state.data.generated_at) ? fresh : state.data;
  async function regenerate() {
    setBusy(true);
    try { setFresh(await api<Summary>(`/summary?fresh=true&range=${period}`)); } catch { /* the current summary stays */ } finally { setBusy(false); }
  }
  async function copy() {
    if (!d) return;
    try { await navigator.clipboard.writeText(asText(d)); setCopied(true); setTimeout(() => setCopied(false), 1600); } catch { /* clipboard blocked */ }
  }
  return (
    <div className="stack ais-page">
      <div className="page-head">
        <div>
          <h1>AI Summary</h1>
          <div className="ais-meta">{d ? `${d.period.label} · data as of ${d.as_of} · ${Object.keys(d.facts).length} figures from ${d.sources.length} pages`
            : "Reading the pages you can open…"}</div>
        </div>
        {d && (
          <div className="row">
            <button type="button" className="btn" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
            <button type="button" className="btn" onClick={regenerate} disabled={busy}>{busy ? "Refreshing…" : "Refresh"}</button>
          </div>
        )}
      </div>
      {state.error && !d && <div className="error">{state.error.message}</div>}
      {!d && !state.error && <div className="card ais-skel"><i /><i /><i /></div>}
      {d && (
        <>
          <SummaryBody key={d.generated_at} d={d} />
          <p className="ais-foot">
            Written from the figures on the linked pages.{" "}
            Every figure opens the panel or row it comes from. {d.note}
          </p>
        </>
      )}
    </div>
  );
}
