"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { Gate, useMe } from "@/components/shell";
import { DataTable, Formula, Head, Kpi, Loading, RecordTable, StatusBadge } from "@/components/ui";
import { download, post, upload, useApi, type Row } from "@/lib/api";
import { compact, label, money } from "@/lib/format";

const TABS: [string, string][] = [
  ["credit", "Credit unlock"], ["requests", "Examiner requests"], ["audit", "Audit files"], ["chain", "Allocation chain"],
  ["bridge", "Forecast bridge"], ["recharge", "Recharge schedule"], ["schedule", "Credit schedule"],
];
type Filing = { FilingId: string; LegalEntity: string; Jurisdiction: string; Status: string; FilingDate: string; CreditRate: number; Authority: string;
  ReviewPeriodStart: string; ReviewPeriodEnd: string; ForecastYearStart: string; ForecastYearEnd: string };

export function TaxView() {
  const params = useSearchParams();
  const router = useRouter();
  const tab = params.get("tab") ?? "credit";
  const filings = useApi<Filing[]>("/tax/filings");
  return (
    <Gate module="tax">
      <Head title="Tax & Transfer Pricing" />
      <div className="tabs">
        {TABS.map(([k, l]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => router.replace(`/tax?tab=${k}`)}>{l}</button>)}
      </div>
      <Loading state={filings}>
        {(fs) => (
          <>
            {tab === "credit" && <CreditUnlock />}
            {tab === "requests" && <ExaminerRequests filings={fs} />}
            {tab === "audit" && <AuditFiles filings={fs} />}
            {tab === "chain" && <Chain />}
            {tab === "bridge" && <Bridge filings={fs} />}
            {tab === "recharge" && <Sections path="/tax/recharge-schedule?year=2026" />}
            {tab === "schedule" && <Sections path="/tax/credit-schedule" />}
          </>
        )}
      </Loading>
    </Gate>
  );
}

/** Any response of { name: rows[], formula }: one table per array, formula as a footnote. */
function Sections({ path }: { path: string }) {
  const state = useApi<Record<string, unknown>>(path);
  return (
    <Loading state={state}>
      {(d) => (
        <div className="stack">
          {Object.entries(d).filter(([, v]) => Array.isArray(v) && v.length > 0 && typeof v[0] === "object").map(([k, v]) => (
            <div key={k} className="card flush"><h2>{label(k).replace(/^\w/, (c) => c.toUpperCase())}</h2><RecordTable rows={v as Row[]} max={200} /></div>
          ))}
          {typeof d.formula === "string" && <Formula>{d.formula}</Formula>}
        </div>
      )}
    </Loading>
  );
}

// ------------------------------------------------------------------------------------------------ credit unlock
type Juris = { Jurisdiction: string; CreditStatus: string; Reference: string; FilingId: string | null; CreditRate: number; RateBasis: string;
  AnnualQualifyingFees: number; Credit: number; Contracts: Row[] };

function CreditUnlock() {
  const state = useApi<{ jurisdictions: Juris[]; credit_unlock: number; credit_claimed: number; formula: string; disclaimer: string }>("/tax/credit-unlock");
  const [off, setOff] = useState<Record<string, boolean>>({});
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Loading state={state}>
      {(e) => {
        const what = e.jurisdictions.filter((j) => j.CreditStatus === "eligible" && !off[j.Jurisdiction]).reduce((a, j) => a + j.Credit, 0);
        const sel = e.jurisdictions.find((j) => j.Jurisdiction === open);
        return (
          <div className="stack">
            <div className="grid g3">
              <Kpi label="Credit unlock (selected)" value={`${compact(what)}/yr`} tone="good" />
              <Kpi label="Claimed today" value={`${compact(e.credit_claimed)}/yr`} />
              <Kpi label="Total" value={`${compact(what + e.credit_claimed)}/yr`} />
            </div>
            <table>
              <thead><tr><th>Jurisdiction</th><th>Status</th><th>Filing</th><th className="num">Qualifying fees /yr</th><th className="num">Credit rate</th>
                <th className="num">Credit /yr</th><th>Include</th></tr></thead>
              <tbody>
                {e.jurisdictions.map((j) => (
                  <tr key={j.Jurisdiction} className={open === j.Jurisdiction ? "focus" : ""}>
                    <td><a href="#contracts" onClick={() => setOpen(j.Jurisdiction)}>{j.Jurisdiction}</a></td>
                    <td><StatusBadge s={j.CreditStatus} /></td>
                    <td className="id">{j.FilingId ?? "—"}</td>
                    <td className="num">{money(j.AnnualQualifyingFees)}</td>
                    <td className="num">{j.CreditRate}%{j.RateBasis && j.RateBasis !== "filing" ? " (assumed)" : ""}</td>
                    <td className="num"><b>{money(j.Credit)}</b></td>
                    <td>{j.CreditStatus === "eligible"
                      ? <input type="checkbox" checked={!off[j.Jurisdiction]} onChange={() => setOff({ ...off, [j.Jurisdiction]: !off[j.Jurisdiction] })} />
                      : <span className="small muted">{j.CreditStatus === "claimed" ? "claimed" : "n/a"}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {sel && (
              <div className="card flush" id="contracts">
                <h2>{sel.Jurisdiction}: contracts <span className="small muted">({sel.Reference})</span></h2>
                <RecordTable rows={sel.Contracts} />
              </div>
            )}
            <Formula>{e.formula} {e.disclaimer}</Formula>
          </div>
        );
      }}
    </Loading>
  );
}

// ------------------------------------------------------------------------------------------------ examiner requests
type ER = { RequestId: string; FilingId: string; Requester: string; RequestText: string; MatchedUseCases: string; Status: string;
  PreparedBy: string; PreparedAt: string; ApprovedBy: string | null;
  Draft: { status: string; headline?: string; narrative?: string; message?: string; confidence?: string; interpreted?: string[];
    use_case?: { id: string; title: string }; result?: { columns: string[]; rows: unknown[][] }; attachments?: { label: string; href: string }[] } };

const SAMPLES = [
  "Provide cloud subscription costs for the review period by vendor, with any invoice differences.",
  "Explain how shared-services technology costs are recharged to the Company and on what basis.",
  "Identify any technology assets that were idle or unused during the review period.",
  "Provide the AI and machine learning costs deducted in the period.",
];

function ExaminerRequests({ filings }: { filings: Filing[] }) {
  const { me, version, refresh } = useMe();
  const [filing, setFiling] = useState(filings[0]?.FilingId ?? "");
  const [requester, setRequester] = useState("Tax examiner");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const list = useApi<ER[]>("/tax/examiner-requests", version);
  const canPrepare = me.actions.includes("datarequest.prepare"), canApprove = me.actions.includes("datarequest.approve");
  async function draft() {
    setBusy(true); setMsg(null);
    try {
      const r = await post<ER>("/tax/examiner-requests", { filing, requester, text });
      setOpenId(r.RequestId); setText(""); refresh();
    } catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
  }
  async function decide(id: string, action: string) {
    setMsg(null);
    try { await post(`/tax/examiner-requests/${id}/decision`, { action }); setMsg(`${id}: ${action} recorded in the ledger.`); refresh(); }
    catch (e) { setMsg((e as Error).message); }
  }
  return (
    <div className="stack">
      {canPrepare && (
        <div className="card">
          <h2>New request</h2>
          <div className="row" style={{ marginBottom: 6 }}>
            <select value={filing} onChange={(e) => setFiling(e.target.value)}>{filings.map((c) => <option key={c.FilingId}>{c.FilingId}</option>)}</select>
            <input value={requester} onChange={(e) => setRequester(e.target.value)} placeholder="Requester" />
          </div>
          <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Request text" maxLength={2000} />
          <div className="small">Samples:{" "}
            {SAMPLES.map((s, i) => <span key={s}>{i > 0 && " · "}<a href="#" onClick={(e) => { e.preventDefault(); setText(s); }}>{s.slice(0, 40)}…</a></span>)}
          </div>
          <p><button className="btn primary" disabled={busy || !text.trim()} onClick={draft}>{busy ? "Drafting…" : "Draft response"}</button></p>
        </div>
      )}
      {msg && <div className="notice">{msg}</div>}
      <Loading state={list}>
        {(rs) => rs.length === 0 ? <p className="muted">No requests.</p> : (
          <table>
            <thead><tr><th>ID</th><th>Filing</th><th>Requester</th><th>Request</th><th>Prepared by</th><th>Status</th></tr></thead>
            <tbody>
              {rs.map((r) => (
                <ExaminerRow key={r.RequestId} r={r} open={openId === r.RequestId} canApprove={canApprove}
                  onToggle={() => setOpenId(openId === r.RequestId ? null : r.RequestId)} onDecide={decide} />
              ))}
            </tbody>
          </table>
        )}
      </Loading>
    </div>
  );
}

function ExaminerRow({ r, open, canApprove, onToggle, onDecide }: { r: ER; open: boolean; canApprove: boolean; onToggle: () => void;
  onDecide: (id: string, action: string) => void }) {
  return (
    <>
      <tr>
        <td className="id"><a href="#" onClick={(e) => { e.preventDefault(); onToggle(); }}>{r.RequestId}</a></td>
        <td className="id">{r.FilingId}</td><td>{r.Requester}</td><td className="small">{r.RequestText}</td>
        <td className="small">{r.PreparedBy}</td><td><StatusBadge s={r.Status} /></td>
      </tr>
      {open && (
        <tr><td colSpan={6}>
          {r.Draft.status !== "drafted" ? <div className="error">{r.Draft.message}</div> : (
            <div className="stack">
              <div className="small muted">{r.Draft.use_case?.id} · {r.Draft.use_case?.title} · confidence {r.Draft.confidence}</div>
              <p>{r.Draft.narrative}</p>
              {r.Draft.result && <DataTable columns={r.Draft.result.columns} rows={r.Draft.result.rows} max={50} />}
              <div className="row">
                {r.Draft.attachments?.map((a) => (
                  <button key={a.href} className="btn" onClick={() => download(a.href.replace(/^\/api/, ""), a.href.endsWith(".zip") ? `${r.FilingId}-audit-file.zip` : `${r.RequestId}.csv`)}>
                    {a.label}</button>
                ))}
                {canApprove && r.Status === "draft" && <>
                  <button className="btn primary" onClick={() => onDecide(r.RequestId, "approve")}>Approve</button>
                  <button className="btn" onClick={() => onDecide(r.RequestId, "return")}>Return</button>
                </>}
              </div>
            </div>
          )}
        </td></tr>
      )}
    </>
  );
}

// ------------------------------------------------------------------------------------------------ audit files
type Pack = { Seq: number; EntryAt: string; Actor: string; FilingId: string; Sha256: string; LedgerHash: string };

function AuditFiles({ filings }: { filings: Filing[] }) {
  const { me, version, refresh } = useMe();
  const packs = useApi<Pack[]>("/tax/audit-files", version);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [verdict, setVerdict] = useState<Row | null>(null);
  const canExport = me.actions.includes("exhibit.export") && me.scope === "enterprise";
  return (
    <div className="stack">
      <div className="card">
        <h2>Export audit file (ZIP, SHA-256 sealed in the ledger)</h2>
        <div className="row">
          {filings.map((c) => (
            <button key={c.FilingId} className="btn primary" disabled={!canExport || !!busy} onClick={async () => {
              setBusy(c.FilingId); setMsg(null);
              try {
                const h = await download(`/tax/filing/${c.FilingId}/audit-file.zip`, `${c.FilingId}-audit-file.zip`);
                setMsg(`${c.FilingId}: SHA-256 ${h.get("x-costiq-sha256")} sealed in ledger entry ${h.get("x-costiq-ledger-seq")}.`);
                refresh();
              } catch (e) { setMsg((e as Error).message); } finally { setBusy(null); }
            }}>{busy === c.FilingId ? "Building…" : c.FilingId}</button>
          ))}
          {!canExport && <span className="small muted">Your role cannot export.</span>}
        </div>
        {msg && <div className="notice" style={{ marginTop: 8 }}>{msg}</div>}
      </div>
      <div className="card">
        <h2>Verify an audit file</h2>
        <input type="file" accept=".zip" onChange={async (e) => {
          const f = e.target.files?.[0];
          if (!f) return;
          try { setVerdict(await upload("/tax/audit-file/verify", f)); } catch (err) { setVerdict({ verdict: (err as Error).message }); }
        }} />
        {verdict && <div className={verdict.recorded ? "notice" : "error"} style={{ marginTop: 8 }}>
          <b>{String(verdict.verdict)}</b><div className="small mono">SHA-256 {String(verdict.sha256 ?? "")}</div></div>}
      </div>
      <div className="card flush">
        <h2>Sealed in the ledger</h2>
        <Loading state={packs}>{(ps) => ps.length === 0 ? <p className="muted" style={{ padding: "0 8px" }}>None yet.</p> : (
          <table>
            <thead><tr><th className="num">Seq</th><th>Filing</th><th>Sealed</th><th>By</th><th>SHA-256</th></tr></thead>
            <tbody>
              {ps.map((p) => (
                <tr key={p.Seq}>
                  <td className="num">{p.Seq}</td><td className="id">{p.FilingId}</td>
                  <td className="small id">{String(p.EntryAt).replace("T", " ").slice(0, 16)}</td><td className="small">{p.Actor}</td>
                  <td><code title={p.Sha256}>{p.Sha256.slice(0, 24)}…</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}</Loading>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ allocation chain
type Chain = { source_total: number; pool: Row[]; opco: Row[]; policies: Row[]; jurisdiction: Row[]; gl: Row[]; exceptions: Row[];
  formula: string; periods: string[]; pending_policies: Row[] };
type Policy = { PolicyId: string; Version: number; Name: string; MatchColumn: string; MatchValue: string; DriverName: string | null;
  FixedEntity: string | null; Priority: number; Status: string; SubmittedBy: string; ApprovedBy: string | null; EffectiveFrom: string; EffectiveTo: string | null };

function Chain() {
  const { me, version, refresh } = useMe();
  const [period, setPeriod] = useState("");
  const state = useApi<Chain>(`/tax/allocation-chain${period ? `?period=${period}` : ""}`, version);
  const pol = useApi<{ policies: Policy[]; drivers: string[] }>("/tax/policies", version);
  const [msg, setMsg] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ pid: string; driver: string } | null>(null);
  async function decide(pid: string, body: Row) {
    setMsg(null);
    try {
      const r = await post<{ policy: Policy; reallocated: Record<string, number> }>(`/tax/policies/${pid}/decision`, body);
      setMsg(`${pid} v${r.policy.Version}: ${body.action} recorded${Object.keys(r.reallocated).length ? `; re-allocated ${Object.keys(r.reallocated).join(", ")}` : ""}.`);
      setDraft(null); refresh();
    } catch (e) { setMsg((e as Error).message); }
  }
  const sum = (rs: Row[] = []) => rs.reduce((a, r) => a + Number(r.AllocatedCost ?? 0), 0);
  return (
    <div className="stack">
      <div className="row">Period:
        <select value={period} onChange={(e) => setPeriod(e.target.value)}><option value="">All periods</option>
          {state.data?.periods.map((p) => <option key={p}>{p}</option>)}</select></div>
      <Loading state={state}>
        {(c) => (
          <>
            <table>
              <thead><tr><th>Step</th><th className="num">Amount</th><th>Detail</th></tr></thead>
              <tbody>
                <tr><td>1. Provider charges</td><td className="num">{money(c.source_total)}</td><td>FOCUS rows and invoices</td></tr>
                <tr><td>2. Shared-services pool</td><td className="num">{money(sum(c.pool))}</td><td>{c.pool.length} pooling methods</td></tr>
                <tr><td>3. Split to entities</td><td className="num">{money(sum(c.opco))}</td><td>{c.policies.length} policies</td></tr>
                <tr><td>4. Jurisdictions</td><td className="num">{money(sum(c.jurisdiction))}</td><td>{c.jurisdiction.length} jurisdictions</td></tr>
                <tr><td>5. GL account + class</td><td className="num">{money(sum(c.gl))}</td>
                  <td>{c.exceptions.length ? `${money(sum(c.exceptions))} exceptions` : "no exceptions"}</td></tr>
              </tbody>
            </table>
            <div className="grid g2">
              <div className="card flush"><h2>By entity</h2><RecordTable rows={c.opco} /></div>
              <div className="card flush"><h2>By jurisdiction</h2><RecordTable rows={c.jurisdiction} /></div>
            </div>
            <div className="card flush"><h2>By GL account</h2><RecordTable rows={c.gl ?? []} /></div>
            {c.exceptions.length > 0 && <div className="card flush"><h2>Exceptions</h2><RecordTable rows={c.exceptions} /></div>}
            <Formula>{c.formula}</Formula>
          </>
        )}
      </Loading>
      {msg && <div className="notice">{msg}</div>}
      <Loading state={pol}>
        {(p) => (
          <div className="card flush">
            <h2>Allocation policies</h2>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Policy</th><th>v</th><th>Matches</th><th>Basis</th><th>Priority</th><th>Effective</th><th>Status</th><th>Actions</th></tr></thead>
                <tbody>
                  {p.policies.map((x) => (
                    <tr key={x.PolicyId + x.Version}>
                      <td><b>{x.PolicyId}</b><div className="small">{x.Name}</div></td>
                      <td>{x.Version}</td>
                      <td className="small">{x.MatchColumn} = {x.MatchValue}</td>
                      <td>{x.DriverName ?? `100% to ${x.FixedEntity}`}</td>
                      <td>{x.Priority}</td>
                      <td className="small id">{String(x.EffectiveFrom).slice(0, 10)}{x.EffectiveTo ? ` to ${String(x.EffectiveTo).slice(0, 10)}` : ""}</td>
                      <td><StatusBadge s={x.Status} /><div className="small muted">{x.Status === "submitted" ? `by ${x.SubmittedBy}` : x.ApprovedBy ? `by ${x.ApprovedBy}` : ""}</div></td>
                      <td>
                        {x.Status === "submitted" && me.actions.includes("rule.approve") && <>
                          <button className="btn primary" onClick={() => decide(x.PolicyId, { action: "approve", version: x.Version })}>Approve</button>
                          <button className="btn" onClick={() => decide(x.PolicyId, { action: "return", version: x.Version })}>Return</button>
                        </>}
                        {x.Status === "approved" && me.actions.includes("driver.submit") && (draft?.pid === x.PolicyId ? <>
                          <select value={draft.driver} onChange={(e) => setDraft({ pid: x.PolicyId, driver: e.target.value })}>
                            {p.drivers.map((d) => <option key={d}>{d}</option>)}</select>{" "}
                          <button className="btn primary" onClick={() => decide(x.PolicyId, { action: "submit", driver: draft.driver })}>Submit</button>
                        </> : <button className="btn" onClick={() => setDraft({ pid: x.PolicyId, driver: x.DriverName ?? p.drivers[0] })}>Propose change</button>)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Loading>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ forecast bridge
type Bridge = { filing: Filing; lines: Row[]; totals: Record<string, number>; verified_savings: Row[]; formula: string };

function Bridge({ filings }: { filings: Filing[] }) {
  const { me } = useMe();
  const [filing, setFiling] = useState(filings[0]?.FilingId ?? "");
  const state = useApi<Bridge>(filing ? `/tax/filing/${filing}/bridge` : null);
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <div className="stack">
      <div className="row">
        <select value={filing} onChange={(e) => setFiling(e.target.value)}>{filings.map((c) => <option key={c.FilingId} value={c.FilingId}>{c.FilingId} · {c.LegalEntity}</option>)}</select>
        {me.actions.includes("exhibit.export") && <button className="btn primary" onClick={() => download(`/tax/filing/${filing}/bridge.csv`, `${filing}-technology-bridge.csv`)
          .then(() => setMsg("Exported and recorded in the ledger.")).catch((e: Error) => setMsg(e.message))}>Export CSV</button>}
        {msg && <span className="small">{msg}</span>}
      </div>
      <Loading state={state}>
        {(b) => {
          const cols = b.lines[0] ? Object.keys(b.lines[0]) : [];
          const total: Row = { [cols[0] ?? "Vendor"]: "Total" };
          cols.forEach((c) => { if (c in b.totals) total[c] = b.totals[c]; });
          const extra = Object.entries(b.totals).filter(([k]) => !cols.includes(k));
          return (
            <>
              <p className="small">{b.filing?.LegalEntity} ({b.filing?.Jurisdiction}) · review period {String(b.filing?.ReviewPeriodStart ?? "").slice(0, 10)} to{" "}
                {String(b.filing?.ReviewPeriodEnd ?? "").slice(0, 10)} · forecast year {String(b.filing?.ForecastYearStart ?? "").slice(0, 10)} to{" "}
                {String(b.filing?.ForecastYearEnd ?? "").slice(0, 10)}</p>
              <div className="card flush"><RecordTable rows={b.lines} total={total} /></div>
              {extra.length > 0 && (
                <table style={{ width: "auto" }}>
                  <tbody>{extra.map(([k, v]) => <tr key={k}><th>{label(k)}</th>
                    <td className="num">{/savings/i.test(k) ? `${compact(v)}/yr` : /exception/i.test(k) ? v : money(v)}</td></tr>)}</tbody>
                </table>
              )}
              <Formula>{b.formula}</Formula>
            </>
          );
        }}
      </Loading>
    </div>
  );
}
