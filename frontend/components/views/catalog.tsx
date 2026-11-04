"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { AutoChart } from "@/components/charts";
import { useCopy } from "@/components/copy";
import { useMe } from "@/components/shell";
import { UseCaseRunner } from "@/components/usecases";
import { DataTable, Formula, Head, Loading } from "@/components/ui";
import { download, post, useApi, type Result } from "@/lib/api";

type Summary = { id: string; title: string; business_question: string; category: string; source: string; module: string; allowed: boolean };

export function CatalogView() {
  const state = useApi<Summary[]>("/usecases");
  const [q, setQ] = useState("");
  const [src, setSrc] = useState("");
  const [cat, setCat] = useState("");
  const [dbt, setDbt] = useState<{ busy: boolean; error?: string }>({ busy: false });
  function downloadDbt() {
    setDbt({ busy: true });
    download("/codebase/dbt.zip", "client-cloud-ledger.zip")
      .then(() => setDbt({ busy: false }))
      .catch((e: Error) => setDbt({ busy: false, error: e.message }));
  }
  return (
    <div className="stack">
      <Head title="Use Case Catalog">
        <button className="btn" onClick={downloadDbt} disabled={dbt.busy}>{dbt.busy ? "Preparing…" : "Download dbt project"}</button>
      </Head>
      {dbt.error && <div className="error">{dbt.error}</div>}
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
                  <Link key={u.id} href={`/catalog/${u.id}`} className="uc">
                    <div className="row" style={{ flexWrap: "nowrap" }}>
                      <span className="uc-id">{u.id}</span>
                      <div>{u.title}</div>
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

export function AskView() {
  const { period } = useMe();
  const examples = useCopy().get<string[]>("catalog.examples") ?? [];  // the tenant's example questions
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [a, setA] = useState<Answer | null>(null);
  const [err, setErr] = useState<string | null>(null);
  async function ask(text: string) {
    setQ(text); setBusy(true); setErr(null);
    try { setA(await post<Answer>("/ask", { question: text, range: period })); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  const res = useMemo(() => a?.result, [a]);
  return (
    <div className="stack">
      <Head title="Ask CostIQ" />
      <div className="card stack">
        <form className="row" onSubmit={(e) => { e.preventDefault(); if (q.trim()) ask(q); }}>
          <input value={q} onChange={(e) => setQ(e.target.value)} maxLength={500} placeholder="Question" style={{ flex: 1 }} />
          <button className="btn primary" disabled={busy || !q.trim()}>{busy ? "Thinking…" : "Ask"}</button>
        </form>
        <div className="small">Examples: {examples.map((x, i) => <span key={x}>{i > 0 && " · "}<a href="#" onClick={(e) => { e.preventDefault(); ask(x); }}>{x}</a></span>)}</div>
      </div>
      {err && <div className="error">{err}</div>}
      {a && a.status !== "answered" && (
        <div className="card"><p>{a.message}</p>
          <ul>{a.suggestions?.map((s) => <li key={s.id}><Link href={`/catalog/${s.id}`}>{s.id} {s.title}</Link></li>)}</ul></div>
      )}
      {a && a.status === "answered" && res && (
        <div className="card stack">
          <div className="small muted">Answered with <Link href={`/catalog/${a.use_case?.id}`}>{a.use_case?.id} · {a.use_case?.title}</Link> · match confidence {a.confidence}
            {a.interpreted?.length ? ` · ${a.interpreted.join(", ")}` : " · the use case's own period"}</div>
          <h2>{a.headline}</h2>
          <AutoChart columns={res.columns} rows={res.rows} />
          <DataTable columns={res.columns} rows={res.rows} max={100} />
          <Formula>{a.formula} Sources: {a.sources?.join(", ")}</Formula>
          {a.alternatives?.length ? <div className="small">Also: {a.alternatives.map((x, i) => <span key={x.id}>{i > 0 && " · "}<Link href={`/catalog/${x.id}`}>{x.id} {x.title}</Link></span>)}</div> : null}
        </div>
      )}
    </div>
  );
}
