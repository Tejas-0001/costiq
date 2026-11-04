"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, download, post, useApi, type Result, type Row } from "@/lib/api";
import { money } from "@/lib/format";
import { AutoChart } from "./charts";
import { PERIODS, useMe } from "./shell";
import { DataTable, Loading, StatusBadge } from "./ui";

type Param = { name: string; type: string; default: string; label: string };
type UseCase = Row & {
  id: string; title: string; business_question: string; source: string; category: string; module: string; allowed: boolean;
  datasets?: string[]; products?: string; catalog_persona?: string; description?: string; params?: Param[]; columns?: string;
  adaptation?: string; notes?: string; reference_url?: string; sql: string; translated_sql: string; published_sql?: string;
};

type Run = Result & { period_note?: string | null };

export function UseCaseRunner({ id, compact }: { id: string; compact?: boolean }) {
  const { period } = useMe();
  const [uc, setUc] = useState<UseCase | null>(null);
  const [params, setParams] = useState<Record<string, string>>({});
  const [res, setRes] = useState<Run | null>(null);
  const [manual, setManual] = useState(false);
  const [err, setErr] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState("result");

  function runWith(p: Record<string, string>) {
    setBusy(true);
    setErr(null);
    setManual(true);  // dates typed in by hand win over the period selector
    post<Run>(`/usecases/${id}/run`, { params: p }).then(setRes).catch(setErr).finally(() => setBusy(false));
  }
  useEffect(() => {
    let live = true;
    api<UseCase>(`/usecases/${id}`).then((u) => {
      if (!live) return;
      setUc(u);
      setRes(null);
      // defaults, except the period: the API fills period_start / period_end from the period selected at the top of the page
      const p: Record<string, string> = {};
      (u.params || []).forEach((x) => { if (x.name !== "period_start" && x.name !== "period_end") p[x.name] = x.default; });
      setParams(p);
      setManual(false);
      if (u.allowed) {
        setBusy(true);
        post<Run>(`/usecases/${id}/run`, { params: p, range: period }).then((r) => {
          if (!live) return;
          setRes(r);
          setParams(Object.fromEntries(Object.entries(r.params ?? p).map(([k, v]) => [k, String(v ?? "")])));
        }).catch((e) => live && setErr(e)).finally(() => live && setBusy(false));
      }
    }).catch((e) => live && setErr(e));
    return () => { live = false; };
  }, [id, period]);

  if (err && !uc) return <div className="error">{err.message}</div>;
  if (!uc) return <div className="muted small">Loading…</div>;
  const qs = new URLSearchParams(params).toString();
  return (
    <div className="stack">
      {!compact && (
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div>
              <div className="uc-id">{uc.id}</div>
              <h1 style={{ fontSize: 20 }}>{uc.title}</h1>
              <div className="muted">{uc.business_question}</div>
            </div>
            <span className="badge b-info">{uc.source}</span>
          </div>
          <div className="small muted" style={{ marginTop: 10 }}>
            {uc.category} · Datasets: {(uc.datasets || []).join(", ")}{uc.products ? ` · Products: ${uc.products}` : ""} · For: {uc.catalog_persona}
          </div>
          {uc.description && <p className="small" style={{ lineHeight: 1.5, marginTop: 8 }}>{uc.description}</p>}
        </div>
      )}
      {!uc.allowed ? <div className="error">Your role cannot run use cases in this module.</div> : (
        <div className="card">
          {(uc.params || []).length > 0 && (
            <div className="row" style={{ marginBottom: 12 }}>
              {(uc.params || []).map((p) => (
                <label key={p.name} className="small muted">
                  {p.label}<br />
                  <input type={p.type === "date" ? "date" : "text"} value={params[p.name] ?? ""}
                    onChange={(e) => setParams({ ...params, [p.name]: e.target.value })} />
                </label>
              ))}
              <button className="btn primary" onClick={() => runWith(params)} disabled={busy} style={{ alignSelf: "flex-end" }}>
                {busy ? "Running…" : "Run"}
              </button>
            </div>
          )}
          <div className="tabs">
            {["result", "sql", "notes"].map((t) => (
              <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t === "sql" ? "SQL" : t[0].toUpperCase() + t.slice(1)}</button>
            ))}
            <div style={{ flex: 1 }} />
            {res && <span className="small muted" style={{ alignSelf: "center" }}>
              {"period_start" in params && !manual ? `${PERIODS.find((x) => x.key === period)?.label} · ` : ""}
              {res.period_note && !manual ? `${res.period_note} · ` : ""}{res.rows.length} rows · {res.elapsed_ms} ms</span>}
            <button className="btn" style={{ marginLeft: 8 }} onClick={() => download(`/usecases/${id}/export.csv?${qs}`, `${id}.csv`)}>Export CSV</button>
          </div>
          {err && <div className="error">{err.message}</div>}
          {tab === "result" && res && (
            <div className="stack">
              <AutoChart columns={res.columns} rows={res.rows} />
              <DataTable columns={res.columns} rows={res.rows} max={compact ? 50 : 500} />
            </div>
          )}
          {tab === "sql" && <SqlTabs uc={uc} />}
          {tab === "notes" && (
            <div className="small" style={{ lineHeight: 1.6 }}>
              <p><b>Columns:</b> {uc.columns || "—"}</p>
              {uc.adaptation && <p><b>Adaptation:</b> {uc.adaptation}</p>}
              {uc.notes && <p><b>Notes:</b> {uc.notes}</p>}
              {uc.reference_url && <p><a href={uc.reference_url} target="_blank" rel="noreferrer">FinOps Foundation reference</a></p>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SqlTabs({ uc }: { uc: UseCase }) {
  const [t, setT] = useState<"sql" | "translated_sql" | "published_sql">("sql");
  const opts: [typeof t, string][] = [["sql", "Executable (portable)"], ["translated_sql", "As run on this database"]];
  if (uc.published_sql) opts.push(["published_sql", "Published by FinOps Foundation"]);
  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        {opts.map(([k, l]) => <button key={k} className={`chip ${t === k ? "on" : ""}`} onClick={() => setT(k)}>{l}</button>)}
      </div>
      <pre className="sql">{uc[t]}</pre>
    </div>
  );
}

type Summary = { id: string; title: string; business_question: string; module: string; source: string; category: string; allowed: boolean };

export function ModuleUseCases({ module, title = "Use cases in this module", filter }: { module: string; title?: string; filter?: (u: Summary) => boolean }) {
  const state = useApi<Summary[]>("/usecases");
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Loading state={state}>
      {(list) => {
        const items = list.filter((u) => u.module === module && (!filter || filter(u)));
        return (
          <div className="card flush">
            <div className="card-head">
              <h2>{title}</h2>
              <span className="muted small">{items.length}</span>
            </div>
            <div className="uc-list">
              {items.map((u) => (
                <div key={u.id}>
                  <div className="uc" style={{ cursor: "pointer" }} onClick={() => setOpen(open === u.id ? null : u.id)}>
                    <div className="row" style={{ flexWrap: "nowrap" }}>
                      <span className="uc-id">{u.id}</span>
                      <div>{u.title}</div>
                    </div>
                    <div className="row" style={{ flexWrap: "nowrap" }}>
                      <Link className="small" href={`/catalog/${u.id}`} onClick={(e) => e.stopPropagation()}>Open</Link>
                      <span className="muted">{open === u.id ? "▾" : "▸"}</span>
                    </div>
                  </div>
                  {open === u.id && <div style={{ padding: "0 8px 8px" }}><UseCaseRunner id={u.id} compact /></div>}
                </div>
              ))}
            </div>
          </div>
        );
      }}
    </Loading>
  );
}

export type Finding = { id: string; title: string; kind: string; severity: string; exposure: number; detail: string; owner: string; formula: string };

export function Findings({ items, onDecided }: { items: Finding[]; onDecided?: () => void }) {
  const { me } = useMe();
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const can = (a: string) => me.actions.includes(a);
  async function decide(f: Finding, action: string) {
    setBusy(f.id + action);
    setMsg(null);
    try {
      const r = await post<{ Seq: number }>(`/findings/${f.id}/decision`, { action, note: `${action} from CostIQ UI` });
      setMsg(`${f.id}: ${action} recorded in the decision ledger (entry ${r.Seq}).`);
      onDecided?.();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }
  if (!items.length) return <div className="muted small">No findings in your scope.</div>;
  return (
    <div className="stack">
      {msg && <div className="notice">{msg}</div>}
      <table>
        <thead><tr><th>ID</th><th>Finding</th><th>Severity</th><th>Kind</th><th className="num">Amount</th><th>Actions</th></tr></thead>
        <tbody>
          {items.map((f) => (
            <tr key={f.id}>
              <td className="id"><Link href={`/catalog/${f.id}`}>{f.id}</Link></td>
              <td title={`${f.detail}
${f.formula}
Owner: ${f.owner}`}>{f.title}</td>
              <td><StatusBadge s={f.severity} /></td>
              <td className="small">{f.kind}</td>
              <td className="num">{money(f.exposure)}</td>
              <td>
                {can("recommendation.submit") && <button className="btn" disabled={!!busy} onClick={() => decide(f, "submit")}>Submit</button>}
                {can("recommendation.endorse") && <button className="btn" disabled={!!busy} onClick={() => decide(f, "endorse")}>Endorse</button>}
                {can("recommendation.approve") && <>
                  <button className="btn primary" disabled={!!busy} onClick={() => decide(f, "approve")}>Approve</button>
                  <button className="btn" disabled={!!busy} onClick={() => decide(f, "return")}>Return</button>
                </>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function LineageDrawer({ chargeKey, onClose }: { chargeKey: string; onClose: () => void }) {
  const state = useApi<{ steps: { step: string; detail: string }[]; charge: Row }>(`/lineage/${chargeKey}`);
  return (
    <>
      <div className="drawer-bg" onClick={onClose} />
      <div className="drawer">
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 14 }}>
          <h2>Charge lineage</h2>
          <button className="btn" onClick={onClose}>Close</button>
        </div>
        <Loading state={state}>
          {(d) => (
            <>
              <ol className="steps">{d.steps.map((s) => <li key={s.step}><b>{s.step}</b><div className="small muted">{s.detail}</div></li>)}</ol>
              <h3>FOCUS record</h3>
              <table><tbody>{Object.entries(d.charge).map(([k, v]) => (
                <tr key={k}><td className="muted small">{k}</td><td className="mono">{typeof v === "object" ? JSON.stringify(v) : String(v)}</td></tr>
              ))}</tbody></table>
            </>
          )}
        </Loading>
      </div>
    </>
  );
}
