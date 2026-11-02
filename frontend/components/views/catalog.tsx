"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { AutoChart } from "@/components/charts";
import { UseCaseRunner } from "@/components/usecases";
import { DataTable, Formula, Head, Loading } from "@/components/ui";
import { post, useApi, type Result } from "@/lib/api";

type Summary = { id: string; title: string; business_question: string; category: string; source: string; module: string; allowed: boolean };

export function CatalogView() {
  const state = useApi<Summary[]>("/usecases");
  const [q, setQ] = useState("");
  const [src, setSrc] = useState("");
  const [cat, setCat] = useState("");
  return (
    <div className="stack">
      <Head title="Use Case Catalog" sub="Every figure in CostIQ is one of these executable, tested queries: FinOps Foundation FOCUS, AI, utilities, prudence and optimization" />
      <Loading state={state}>
        {(list) => {
          const cats = [...new Set(list.map((u) => u.category))].sort();
          const sources = [...new Set(list.map((u) => u.source))].sort();
          const shown = list.filter((u) => (!src || u.source === src) && (!cat || u.category === cat) &&
            (!q || `${u.id} ${u.title} ${u.business_question}`.toLowerCase().includes(q.toLowerCase())));
          return (
            <>
              <div className="card row">
                <input placeholder="Search use cases" value={q} onChange={(e) => setQ(e.target.value)} style={{ flex: 1, minWidth: 220 }} />
                <select value={src} onChange={(e) => setSrc(e.target.value)}><option value="">All sources</option>{sources.map((s) => <option key={s}>{s}</option>)}</select>
                <select value={cat} onChange={(e) => setCat(e.target.value)}><option value="">All categories</option>{cats.map((s) => <option key={s}>{s}</option>)}</select>
                <span className="small muted">{shown.length} of {list.length}</span>
              </div>
              <div className="card flush uc-list">
                {shown.map((u) => (
                  <Link key={u.id} href={`/catalog/${u.id}`} className="uc" style={{ textDecoration: "none", color: "inherit", opacity: u.allowed ? 1 : 0.55 }}>
                    <div className="row" style={{ flexWrap: "nowrap" }}>
                      <span className="uc-id">{u.id}</span>
                      <div><div style={{ fontWeight: 600, fontSize: 13.5 }}>{u.title}</div><div className="muted small">{u.business_question}</div></div>
                    </div>
                    <span className="small muted" style={{ whiteSpace: "nowrap" }}>{u.allowed ? u.category : "not in your role"}</span>
                  </Link>
                ))}
              </div>
            </>
          );
        }}
      </Loading>
    </div>
  );
}

export function UseCasePage({ id }: { id: string }) {
  return (
    <div className="stack">
      <Link href="/catalog" className="small">← Catalog</Link>
      <UseCaseRunner id={id} />
    </div>
  );
}

type Answer = { status: string; message?: string; suggestions?: { id: string; title: string }[]; use_case?: { id: string; title: string };
  confidence?: string; interpreted?: string[]; headline?: string; result?: Result; formula?: string; sources?: string[]; alternatives?: { id: string; title: string }[] };

const EXAMPLES = [
  "Which vendors are missing CIP-013 clauses?", "How much spend is at risk of disallowance?", "What could cloud fees earn as a regulatory asset?",
  "Are regulated OpCos subsidizing renewables?", "Which GPU endpoints are idle?", "Cost per accepted outcome by LLM app",
  "Storm cost recovery", "Unused Copilot seats", "Which non-production servers run 24x7?", "Rate case test year bridge by vendor",
];

export function AskView() {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [a, setA] = useState<Answer | null>(null);
  const [err, setErr] = useState<string | null>(null);
  async function ask(text: string) {
    setQ(text); setBusy(true); setErr(null);
    try { setA(await post<Answer>("/ask", { question: text })); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  const res = useMemo(() => a?.result, [a]);
  return (
    <div className="stack">
      <Head title="Ask CostIQ" sub="Plain-English questions answered by the catalog's tested queries. Deterministic: no LLM, and the question never reaches SQL." />
      <div className="card stack">
        <form className="ask-box" onSubmit={(e) => { e.preventDefault(); if (q.trim()) ask(q); }}>
          <input value={q} onChange={(e) => setQ(e.target.value)} maxLength={500} placeholder="e.g. Which vendors are missing CIP-013 clauses?" />
          <button className="btn primary" disabled={busy || !q.trim()}>{busy ? "Thinking…" : "Ask"}</button>
        </form>
        <div className="row">{EXAMPLES.map((x) => <button key={x} className="chip" onClick={() => ask(x)}>{x}</button>)}</div>
      </div>
      {err && <div className="error">{err}</div>}
      {a && a.status !== "answered" && (
        <div className="card"><p>{a.message}</p>
          <div className="row">{a.suggestions?.map((s) => <Link key={s.id} className="chip" href={`/catalog/${s.id}`}>{s.id} · {s.title}</Link>)}</div></div>
      )}
      {a && a.status === "answered" && res && (
        <div className="card stack">
          <div className="small muted">Answered with <Link href={`/catalog/${a.use_case?.id}`}>{a.use_case?.id} · {a.use_case?.title}</Link> · match confidence {a.confidence}
            {a.interpreted?.length ? ` · ${a.interpreted.join(", ")}` : ""}</div>
          <h2 style={{ margin: 0 }}>{a.headline}</h2>
          <AutoChart columns={res.columns} rows={res.rows} />
          <DataTable columns={res.columns} rows={res.rows} max={100} />
          <Formula>{a.formula} Sources: {a.sources?.join(", ")}</Formula>
          {a.alternatives?.length ? <div className="row small">Also relevant: {a.alternatives.map((x) => <Link key={x.id} href={`/catalog/${x.id}`} className="chip">{x.id} · {x.title}</Link>)}</div> : null}
        </div>
      )}
    </div>
  );
}
