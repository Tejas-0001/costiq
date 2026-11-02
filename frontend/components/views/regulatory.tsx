"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { BarChart, LineChart } from "@/components/charts";
import { Gate, useMe } from "@/components/shell";
import { DataTable, Formula, Head, Kpi, Loading, RecordTable, StatusBadge } from "@/components/ui";
import { download, post, upload, useApi, type Row } from "@/lib/api";
import { compact, money } from "@/lib/format";

const TABS: [string, string][] = [
  ["earnings", "Earnings Unlock"], ["requests", "Data Requests"], ["pack", "Evidence Pack"], ["chain", "Allocation chain"],
  ["bridge", "Rate-case bridge"], ["form60", "Form 60 schedule"], ["assets", "Regulatory assets"],
];
type RateCase = { Docket: string; LegalEntity: string; Jurisdiction: string; Status: string; FilingDate: string; PreTaxRor: number;
  BasePeriodStart: string; BasePeriodEnd: string; TestYearStart: string; TestYearEnd: string };

export function RegulatoryView() {
  const params = useSearchParams();
  const router = useRouter();
  const tab = params.get("tab") ?? "earnings";
  const cases = useApi<RateCase[]>("/regulatory/rate-cases");
  return (
    <Gate module="regulatory">
      <Head title="Regulatory Accounting" sub="Rate-case evidence, earnings on cloud fees, affiliate allocation and data requests" />
      <div className="tabs">
        {TABS.map(([k, l]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => router.replace(`/regulatory?tab=${k}`)}>{l}</button>)}
      </div>
      <Loading state={cases}>
        {(rc) => (
          <>
            {tab === "earnings" && <Earnings />}
            {tab === "requests" && <DataRequests cases={rc} />}
            {tab === "pack" && <EvidencePacks cases={rc} />}
            {tab === "chain" && <Chain />}
            {tab === "bridge" && <Bridge cases={rc} />}
            {tab === "form60" && <Form60 />}
            {tab === "assets" && <Assets />}
          </>
        )}
      </Loading>
    </Gate>
  );
}

// ------------------------------------------------------------------------------------------------ earnings unlock
type Juris = { Jurisdiction: string; DeferralStatus: string; OrderReference: string; Docket: string | null; PreTaxRor: number; RorBasis: string;
  AnnualSubscriptionFees: number; Year1Return: number; SteadyStateReturn: number; Earned: boolean;
  Contracts: { Vendor: string; LegalEntity: string; AnnualFees: number; AmortizationMonths: number }[] };

function Earnings() {
  const state = useApi<{ jurisdictions: Juris[]; earnings_unlock: number; earned_today: number; formula: string; disclaimer: string }>("/regulatory/earnings-unlock");
  const [off, setOff] = useState<Record<string, boolean>>({});
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Loading state={state}>
      {(e) => {
        const what = e.jurisdictions.filter((j) => !j.Earned && !off[j.Jurisdiction]).reduce((a, j) => a + j.SteadyStateReturn, 0);
        const sel = e.jurisdictions.find((j) => j.Jurisdiction === open);
        return (
          <div className="stack">
            <div className="grid g3">
              <Kpi label="Earnings unlock (what-if)" value={`${compact(what)}/yr`} tone="good"
                sub="steady-state return if the selected states allowed deferral" />
              <Kpi label="Earned today under order" value={`${compact(e.earned_today)}/yr`} sub="jurisdictions that already allow deferral" />
              <Kpi label="Capex bias removed" value={`${compact(what + e.earned_today)}/yr`} sub="total return cloud subscriptions could earn" />
            </div>
            <div className="card flush">
              <div style={{ padding: "14px 16px 6px" }}><h2 style={{ margin: 0 }}>What-if by state</h2><span className="small muted">toggle a state to include it in the unlock</span></div>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>State</th><th>Status</th><th>Docket</th><th className="num">Annual fees</th><th className="num">Pre-tax return</th><th className="num">Year 1</th><th className="num">Steady state</th><th>Include</th></tr></thead>
                  <tbody>
                    {e.jurisdictions.map((j) => (
                      <tr key={j.Jurisdiction} onClick={() => setOpen(j.Jurisdiction)} style={{ cursor: "pointer" }}>
                        <td><b>{j.Jurisdiction}</b></td>
                        <td><StatusBadge s={j.Earned ? "earned under order" : j.DeferralStatus === "rejected" ? "rejected: re-file" : "not yet requested"} /></td>
                        <td className="small id">{j.Docket ?? <span className="muted">none</span>}</td>
                        <td className="num">{money(j.AnnualSubscriptionFees)}</td>
                        <td className="num">{j.PreTaxRor}%{j.RorBasis !== "docket" && <div className="small muted">assumed</div>}</td>
                        <td className="num">{money(j.Year1Return)}</td>
                        <td className="num"><b>{money(j.SteadyStateReturn)}</b></td>
                        <td onClick={(ev) => ev.stopPropagation()}>{j.Earned ? <span className="small muted">earned</span>
                          : <input type="checkbox" checked={!off[j.Jurisdiction]} onChange={() => setOff({ ...off, [j.Jurisdiction]: !off[j.Jurisdiction] })} />}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div className="card">
              <h2>Return on deferred cloud subscription fees by state</h2>
              <BarChart labels={e.jurisdictions.map((j) => j.Jurisdiction)} stacked height="short"
                datasets={[
                  { label: "Earned under order", data: e.jurisdictions.map((j) => (j.Earned ? j.SteadyStateReturn : 0)), backgroundColor: "#20b26b" },
                  { label: "Unlock if deferral allowed", data: e.jurisdictions.map((j) => (!j.Earned && !off[j.Jurisdiction] ? j.SteadyStateReturn : 0)), backgroundColor: "#2f6bff" },
                ]} />
            </div>
            {sel && (
              <div className="card flush">
                <div style={{ padding: "14px 16px 6px" }} className="card-head"><h2 style={{ margin: 0 }}>{sel.Jurisdiction}: contracts and amortization</h2>
                  <span className="small muted">{sel.OrderReference}</span></div>
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

// ------------------------------------------------------------------------------------------------ data requests
type DR = { RequestId: string; Docket: string; Requester: string; RequestText: string; MatchedUseCases: string; Status: string;
  PreparedBy: string; PreparedAt: string; ApprovedBy: string | null;
  Draft: { status: string; headline?: string; narrative?: string; message?: string; confidence?: string; interpreted?: string[];
    use_case?: { id: string; title: string }; result?: { columns: string[]; rows: unknown[][] }; attachments?: { label: string; href: string }[] } };

const SAMPLES = [
  "Provide cloud subscription costs for the test year by vendor, with any invoice differences.",
  "Explain how service-company technology costs are allocated to the Company and on what basis.",
  "Identify any technology assets that were idle or unused during the base period.",
  "Provide the amount of AI and machine learning costs included in the test year.",
];

function DataRequests({ cases }: { cases: RateCase[] }) {
  const { me, version, refresh } = useMe();
  const [docket, setDocket] = useState(cases[0]?.Docket ?? "");
  const [requester, setRequester] = useState("Commission staff");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const list = useApi<DR[]>("/regulatory/data-requests", version);
  const canPrepare = me.actions.includes("datarequest.prepare"), canApprove = me.actions.includes("datarequest.approve");
  async function draft() {
    setBusy(true); setMsg(null);
    try {
      const r = await post<DR>("/regulatory/data-requests", { docket, requester, text });
      setOpenId(r.RequestId); setText(""); refresh();
    } catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
  }
  async function decide(id: string, action: string) {
    setMsg(null);
    try { await post(`/regulatory/data-requests/${id}/decision`, { action }); setMsg(`${id}: ${action} recorded in the ledger.`); refresh(); }
    catch (e) { setMsg((e as Error).message); }
  }
  return (
    <div className="stack">
      {canPrepare && (
        <div className="card">
          <h2>Draft a response</h2>
          <div className="row" style={{ marginBottom: 8 }}>
            <select value={docket} onChange={(e) => setDocket(e.target.value)}>{cases.map((c) => <option key={c.Docket}>{c.Docket}</option>)}</select>
            <input value={requester} onChange={(e) => setRequester(e.target.value)} placeholder="Requester" />
          </div>
          <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste the data request text" maxLength={2000} />
          <div className="row" style={{ marginTop: 8 }}>
            {SAMPLES.map((s) => <button key={s} className="chip" onClick={() => setText(s)}>{s.slice(0, 48)}…</button>)}
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn primary" disabled={busy || !text.trim()} onClick={draft}>{busy ? "Drafting…" : "Draft response"}</button>
            <span className="small muted">Deterministic matching against the use-case catalog; no LLM, nothing leaves your boundary.</span>
          </div>
        </div>
      )}
      {msg && <div className="pill">{msg}</div>}
      <Loading state={list}>
        {(rs) => rs.length === 0 ? <div className="muted small">No data requests yet.</div> : (
          <div className="stack">
            {rs.map((r) => (
              <div key={r.RequestId} className="card">
                <div className="card-head" style={{ cursor: "pointer" }} onClick={() => setOpenId(openId === r.RequestId ? null : r.RequestId)}>
                  <div><span className="uc-id">{r.RequestId}</span> <b>{r.Requester}</b> <span className="muted small">· {r.Docket} · prepared by {r.PreparedBy}</span>
                    <div className="small" style={{ marginTop: 4 }}>{r.RequestText}</div></div>
                  <StatusBadge s={r.Status} />
                </div>
                {openId === r.RequestId && (
                  <div className="stack">
                    {r.Draft.status !== "drafted" ? <div className="error">{r.Draft.message}</div> : (
                      <>
                        <div className="small muted">Answered with {r.Draft.use_case?.id} · {r.Draft.use_case?.title} · confidence {r.Draft.confidence}
                          {r.Draft.interpreted?.length ? ` · ${r.Draft.interpreted.join(", ")}` : ""}</div>
                        <div className="card" style={{ background: "var(--panel-2)" }}><b>Draft response</b><p style={{ lineHeight: 1.55, marginTop: 6 }}>{r.Draft.narrative}</p></div>
                        {r.Draft.result && <DataTable columns={r.Draft.result.columns} rows={r.Draft.result.rows} max={50} />}
                        <div className="row">
                          {r.Draft.attachments?.map((a) => (
                            <button key={a.href} className="btn sm" onClick={() => download(a.href.replace(/^\/api/, ""), a.href.endsWith(".zip") ? `${r.Docket}-evidence-pack.zip` : `${r.RequestId}.csv`)}>
                              Attach: {a.label}</button>
                          ))}
                          <div style={{ flex: 1 }} />
                          {canApprove && r.Status === "draft" && <>
                            <button className="btn sm primary" onClick={() => decide(r.RequestId, "approve")}>Approve response</button>
                            <button className="btn sm" onClick={() => decide(r.RequestId, "return")}>Return</button>
                          </>}
                        </div>
                      </>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Loading>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ evidence pack
type Pack = { Seq: number; EntryAt: string; Actor: string; Docket: string; Sha256: string; LedgerHash: string };

function EvidencePacks({ cases }: { cases: RateCase[] }) {
  const { me, version, refresh } = useMe();
  const packs = useApi<Pack[]>("/regulatory/evidence-packs", version);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [verdict, setVerdict] = useState<Row | null>(null);
  const canExport = me.actions.includes("exhibit.export") && me.scope === "enterprise";
  return (
    <div className="stack">
      <div className="card">
        <h2>One-click Rate-Case Evidence Pack</h2>
        <p className="small muted" style={{ lineHeight: 1.5 }}>A ZIP per docket: the test-year bridge exhibit, a workpaper per vendor (invoice tie-out and every
          allocation step), approvals with ledger hashes, optimization actions, the prudence summary, a ledger certificate and a methodology note.
          Its SHA-256 is sealed in the hash-chained ledger, so anyone can later prove a filed exhibit was not altered.</p>
        <div className="row">
          {cases.map((c) => (
            <button key={c.Docket} className="btn primary" disabled={!canExport || !!busy} onClick={async () => {
              setBusy(c.Docket); setMsg(null);
              try {
                const h = await download(`/regulatory/rate-case/${c.Docket}/evidence-pack.zip`, `${c.Docket}-evidence-pack.zip`);
                setMsg(`${c.Docket}: pack exported. SHA-256 ${h.get("x-costiq-sha256")} sealed in ledger entry ${h.get("x-costiq-ledger-seq")}.`);
                refresh();
              } catch (e) { setMsg((e as Error).message); } finally { setBusy(null); }
            }}>{busy === c.Docket ? "Building…" : `Export ${c.Docket}`}</button>
          ))}
          {!canExport && <span className="small muted">Your role cannot export exhibits.</span>}
        </div>
        {msg && <div className="notice" style={{ marginTop: 10, wordBreak: "break-all" }}>{msg}</div>}
      </div>
      <div className="grid g2w">
        <div className="card">
          <h2>Verify a pack</h2>
          <p className="small muted">Upload a pack received from anyone (a filing, an intervenor, an auditor). CostIQ hashes it and checks the ledger.</p>
          <input type="file" accept=".zip" onChange={async (e) => {
            const f = e.target.files?.[0];
            if (!f) return;
            try { setVerdict(await upload("/regulatory/evidence-pack/verify", f)); } catch (err) { setVerdict({ verdict: (err as Error).message }); }
          }} />
          {verdict && <div className={verdict.recorded ? "notice" : "error"} style={{ marginTop: 10, wordBreak: "break-all" }}>
            <b>{String(verdict.verdict)}</b><div className="small">SHA-256 {String(verdict.sha256 ?? "")}</div></div>}
        </div>
        <div className="card flush">
          <div style={{ padding: "14px 16px 6px" }}><h2 style={{ margin: 0 }}>Packs sealed in the ledger</h2></div>
          <Loading state={packs}>{(ps) => ps.length === 0 ? <p className="small muted" style={{ padding: "0 16px 14px" }}>No packs exported yet.</p> : (
            <div className="table-wrap">
              <table>
                <thead><tr><th className="num">Seq</th><th>Docket</th><th>Sealed</th><th>SHA-256</th></tr></thead>
                <tbody>
                  {ps.map((p) => (
                    <tr key={p.Seq}>
                      <td className="num">{p.Seq}</td>
                      <td className="id"><b>{p.Docket}</b></td>
                      <td className="small">{String(p.EntryAt).replace("T", " ").slice(0, 16)}<div className="muted">{p.Actor}</div></td>
                      <td><code className="small" title={p.Sha256}>{p.Sha256.slice(0, 24)}…</code></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}</Loading>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ allocation chain
type Chain = { source_total: number; pool: Row[]; opco: Row[]; policies: Row[]; jurisdiction: Row[]; ferc: Row[]; exceptions: Row[];
  formula: string; periods: string[]; pending_policies: Row[] };
type Policy = { PolicyId: string; Version: number; Name: string; MatchColumn: string; MatchValue: string; DriverName: string | null;
  FixedEntity: string | null; Priority: number; Status: string; SubmittedBy: string; ApprovedBy: string | null; EffectiveFrom: string; EffectiveTo: string | null };

function Chain() {
  const { me, version, refresh } = useMe();
  const [period, setPeriod] = useState("");
  const state = useApi<Chain>(`/regulatory/allocation-chain${period ? `?period=${period}` : ""}`, version);
  const pol = useApi<{ policies: Policy[]; drivers: string[] }>("/regulatory/policies", version);
  const [msg, setMsg] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ pid: string; driver: string } | null>(null);
  async function decide(pid: string, body: Row) {
    setMsg(null);
    try {
      const r = await post<{ policy: Policy; reallocated: Record<string, number> }>(`/regulatory/policies/${pid}/decision`, body);
      setMsg(`${pid} v${r.policy.Version}: ${body.action} recorded${Object.keys(r.reallocated).length ? `; re-allocated ${Object.keys(r.reallocated).join(", ")}` : ""}.`);
      setDraft(null); refresh();
    } catch (e) { setMsg((e as Error).message); }
  }
  const sum = (rs: Row[]) => rs.reduce((a, r) => a + Number(r.AllocatedCost ?? 0), 0);
  return (
    <div className="stack">
      <div className="row"><span className="small muted">Period</span>
        <select value={period} onChange={(e) => setPeriod(e.target.value)}><option value="">All periods</option>
          {state.data?.periods.map((p) => <option key={p}>{p}</option>)}</select></div>
      <Loading state={state}>
        {(c) => (
          <>
            <div className="chain">
              <div className="step"><div className="n">1</div><b>Provider charges</b><div className="amt">{money(c.source_total)}</div><div className="small muted">FOCUS rows and invoices</div></div>
              <div className="step"><div className="n">2</div><b>Service-company pool</b><div className="amt">{money(sum(c.pool))}</div><div className="small muted">{c.pool.length} pooling methods</div></div>
              <div className="step new"><div className="n">3</div><b>Split to OpCos</b><div className="amt">{money(sum(c.opco))}</div><div className="small muted">{c.policies.length} policies, driver shares</div></div>
              <div className="step new"><div className="n">4</div><b>Jurisdictions</b><div className="amt">{money(sum(c.jurisdiction))}</div><div className="small muted">{c.jurisdiction.length} jurisdictions</div></div>
              <div className="step new"><div className="n">5</div><b>FERC account + class</b><div className="amt">{money(sum(c.ferc))}</div>
                <div className="small muted">{c.exceptions.length ? `${money(sum(c.exceptions))} exceptions` : "no exceptions"}</div></div>
            </div>
            <div className="grid g2">
              <div className="card"><h2>By operating company</h2><BarChart horizontal height="short" labels={c.opco.map((r) => String(r.LegalEntity))}
                datasets={[{ label: "Allocated", data: c.opco.map((r) => Number(r.AllocatedCost)) }]} /></div>
              <div className="card"><h2>By jurisdiction</h2><BarChart horizontal height="short" labels={c.jurisdiction.map((r) => String(r.Jurisdiction))}
                datasets={[{ label: "Allocated", data: c.jurisdiction.map((r) => Number(r.AllocatedCost)), backgroundColor: "#8a5cff" }]} /></div>
            </div>
            <div className="card flush"><div style={{ padding: "14px 16px 4px" }}><h2 style={{ margin: 0 }}>By FERC account</h2></div><RecordTable rows={c.ferc} /></div>
            <Formula>{c.formula}</Formula>
          </>
        )}
      </Loading>
      {msg && <div className="pill">{msg}</div>}
      <Loading state={pol}>
        {(p) => (
          <div className="card flush">
            <div style={{ padding: "14px 16px 6px" }} className="card-head"><h2 style={{ margin: 0 }}>Allocation policies</h2>
              <span className="small muted">submit by service-company accounting · approve by the controller · separation of duties enforced</span></div>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Policy</th><th>v</th><th>Matches</th><th>Basis</th><th>Priority</th><th>Effective</th><th>Status</th><th>Actions</th></tr></thead>
                <tbody>
                  {p.policies.map((x) => (
                    <tr key={x.PolicyId + x.Version}>
                      <td><b>{x.PolicyId}</b><div className="small muted">{x.Name}</div></td>
                      <td>{x.Version}</td>
                      <td className="small">{x.MatchColumn} = {x.MatchValue}</td>
                      <td>{x.DriverName ?? `100% to ${x.FixedEntity}`}</td>
                      <td>{x.Priority}</td>
                      <td className="small">{String(x.EffectiveFrom).slice(0, 10)}{x.EffectiveTo ? ` → ${String(x.EffectiveTo).slice(0, 10)}` : ""}</td>
                      <td><StatusBadge s={x.Status} /><div className="small muted">{x.Status === "submitted" ? `by ${x.SubmittedBy}` : x.ApprovedBy ? `by ${x.ApprovedBy}` : ""}</div></td>
                      <td>
                        <div className="row">
                          {x.Status === "submitted" && me.actions.includes("rule.approve") && <>
                            <button className="btn sm primary" onClick={() => decide(x.PolicyId, { action: "approve", version: x.Version })}>Approve</button>
                            <button className="btn sm" onClick={() => decide(x.PolicyId, { action: "return", version: x.Version })}>Return</button>
                          </>}
                          {x.Status === "approved" && me.actions.includes("driver.submit") && (draft?.pid === x.PolicyId ? <>
                            <select value={draft.driver} onChange={(e) => setDraft({ pid: x.PolicyId, driver: e.target.value })}>
                              {p.drivers.map((d) => <option key={d}>{d}</option>)}</select>
                            <button className="btn sm primary" onClick={() => decide(x.PolicyId, { action: "submit", driver: draft.driver })}>Submit</button>
                          </> : <button className="btn sm" onClick={() => setDraft({ pid: x.PolicyId, driver: x.DriverName ?? p.drivers[0] })}>Propose change</button>)}
                        </div>
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

// ------------------------------------------------------------------------------------------------ bridge, form 60, assets
type Bridge = { docket: RateCase; lines: Row[]; totals: Record<string, number>; verified_savings: Row[]; formula: string };

function Bridge({ cases }: { cases: RateCase[] }) {
  const { me } = useMe();
  const [docket, setDocket] = useState(cases[0]?.Docket ?? "");
  const state = useApi<Bridge>(docket ? `/regulatory/rate-case/${docket}/bridge` : null);
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <div className="stack">
      <div className="row">
        <select value={docket} onChange={(e) => setDocket(e.target.value)}>{cases.map((c) => <option key={c.Docket} value={c.Docket}>{c.Docket} · {c.LegalEntity}</option>)}</select>
        {me.actions.includes("exhibit.export") && <button className="btn primary" onClick={() => download(`/regulatory/rate-case/${docket}/bridge.csv`, `${docket}-technology-om-bridge.csv`)
          .then(() => setMsg("Exhibit exported and recorded in the ledger.")).catch((e: Error) => setMsg(e.message))}>Export exhibit CSV</button>}
        {msg && <span className="pill">{msg}</span>}
      </div>
      <Loading state={state}>
        {(b) => (
          <>
            <div className="grid g4">
              <Kpi label="Base period actual" value={money(b.totals.BasePeriodActual)} sub={`${String(b.docket.BasePeriodStart).slice(0, 10)} to ${String(b.docket.BasePeriodEnd).slice(0, 10)}`} />
              <Kpi label="Test-year forecast" value={money(b.totals.TestYearForecast)} sub={`${money(b.totals.KnownAndMeasurable)} known and measurable`} />
              <Kpi label="Invoice exceptions" value={b.totals.InvoiceExceptions} tone={b.totals.InvoiceExceptions ? "bad" : "good"} sub="vendors not tied to invoices" />
              <Kpi label="Verified savings achieved" value={`${compact(b.totals.VerifiedSavingsAnnual)}/yr`} tone="good" sub="prudence evidence from the Optimization Center" />
            </div>
            <div className="card flush">
              <div style={{ padding: "14px 16px 6px" }}><h2 style={{ margin: 0 }}>Technology O&M: base period to test year, by vendor</h2>
                <span className="small muted">{b.docket.LegalEntity} ({b.docket.Jurisdiction}) · exhibit UT-05</span></div>
              <RecordTable rows={b.lines} total={{ Vendor: "Total", BasePeriodActual: b.totals.BasePeriodActual, BaseAnnualized: b.totals.BaseAnnualized,
                TestYearForecast: b.totals.TestYearForecast, KnownAndMeasurable: b.totals.KnownAndMeasurable }} />
            </div>
            <Formula>{b.formula}</Formula>
          </>
        )}
      </Loading>
    </div>
  );
}

function Form60() {
  const state = useApi<{ year: number; lines: Row[]; by_company: Row[]; formula: string }>("/regulatory/form60?year=2026");
  return (
    <Loading state={state}>
      {(f) => (
        <div className="stack">
          <div className="grid g2">
            <div className="card"><h2>Billed to each associate company ({f.year})</h2>
              <BarChart horizontal labels={f.by_company.map((r) => String(r.AssociateCompany))}
                datasets={[{ label: "Amount billed", data: f.by_company.map((r) => Number(r.AmountBilled)) }]} /></div>
            <div className="card flush"><div style={{ padding: "14px 16px 4px" }}><h2 style={{ margin: 0 }}>Schedule XXI: methods of allocation</h2></div>
              <RecordTable rows={f.lines} /></div>
          </div>
          <Formula>{f.formula}</Formula>
        </div>
      )}
    </Loading>
  );
}

function Assets() {
  const state = useApi<{ by_jurisdiction: Row[]; schedule: Row[]; lines: Row[]; formula: string }>("/regulatory/regulatory-assets");
  return (
    <Loading state={state}>
      {(a) => (
        <div className="stack">
          <div className="grid g2">
            <div className="card flush"><div style={{ padding: "14px 16px 4px" }}><h2 style={{ margin: 0 }}>Deferred to Account 182.3 by state</h2></div>
              <RecordTable rows={a.by_jurisdiction} /></div>
            <div className="card"><h2>Regulatory asset balance and amortization</h2>
              <LineChart labels={a.schedule.map((s) => String(s.Month))}
                datasets={[{ label: "Ending balance", data: a.schedule.map((s) => Number(s.EndingBalance)), fill: false },
                  { label: "Monthly amortization", data: a.schedule.map((s) => Number(s.Amortization)) }]} /></div>
          </div>
          <div className="card flush"><div style={{ padding: "14px 16px 4px" }}><h2 style={{ margin: 0 }}>Deferral lines and terms</h2></div>
            <RecordTable rows={a.lines} max={100} /></div>
          <Formula>{a.formula}</Formula>
        </div>
      )}
    </Loading>
  );
}
