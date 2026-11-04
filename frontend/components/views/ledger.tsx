"use client";
// Bill unbundling and tag enforcement: the invoice-to-ledger drawer (Reconciliation), the tag-compliance panel with
// suspense and the pooling-rule proposals with maker-checker (Allocation). Tenant words come from the copy fragment
// ledger.json (reconciliation.*, allocation.*); the panels render only when the API reports the tenant data behind them.
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useCopy } from "@/components/copy";
import { PERIODS, useMe, type PeriodInfo } from "@/components/shell";
import { LineageDrawer } from "@/components/usecases";
import { Formula, Kpi, Loading, RecordTable, StatusBadge } from "@/components/ui";
import { post, useApi, type Row } from "@/lib/api";
import { money } from "@/lib/format";
import "@/app/ledger.css";

const str = (v: unknown) => (v == null || v === "" ? "—" : String(v));
const num = (v: unknown) => Number(v ?? 0);

// ------------------------------------------------------------------------------------------------ invoice to ledger
type Group = {
  LegalEntity: string; FunctionCode?: string | null; GlAccount: string | null; GlTitle: string | null; CostClass: string | null;
  RuleId?: string | null; Citation?: string | null; PolicyFlag?: string | null; BillingClass?: string | null;
  Exception: string | null; Lines: number; AllocatedCost: number;
};
type Charge = { ChargeKey: string; Day: string; Service: string; Item: string | null; CostCenter: string; Billed: number; Effective: number };
type InvoiceLedger = {
  invoice: { InvoiceId: string; InvoiceDetailId: string; Issuer: string; Period: string; ChargeCategory: string; IssueStatus: string;
    Invoiced: number; Status?: string; Label?: string | null };
  totals: { invoiced: number; billed_rows: number; effective_rows: number; allocated: number; difference: number; charge_lines: number };
  groups: Group[]; by_entity: { LegalEntity: string; AllocatedCost: number }[]; residuals: Row[]; charges: Charge[]; formula: string;
};

/** One invoice line unbundled into the ledger: company, function, account, rule and citation; reconciling items; the
 *  largest charges, each of which opens the charge lineage drawer on top. */
export function InvoiceLedgerDrawer({ detailId, onClose }: { detailId: string; onClose: () => void }) {
  const { t } = useCopy();
  const state = useApi<InvoiceLedger>(`/reconciliation/${encodeURIComponent(detailId)}/ledger`);
  const [lineage, setLineage] = useState<string | null>(null);
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === "Escape" && !lineage) onClose(); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [lineage, onClose]);
  return (
    <>
      <div className="drawer-bg" onClick={onClose} />
      <div className="drawer ledger-drawer" role="dialog" aria-label={t("reconciliation.drawerTitle")}>
        <div className="ledger-head">
          <div>
            <h2>{t("reconciliation.drawerTitle")}</h2>
            {state.data && (
              <div className="ledger-sub">
                <span>{state.data.invoice.Issuer} · <span className="mono">{state.data.invoice.InvoiceId}</span> · {state.data.invoice.ChargeCategory} · {state.data.invoice.Period}</span>
                {state.data.invoice.Status && <StatusBadge s={state.data.invoice.Status} />}
                {state.data.invoice.Label && <span>{state.data.invoice.Label}</span>}
              </div>
            )}
          </div>
          <button className="btn" onClick={onClose}>Close</button>
        </div>
        <Loading state={state}>{(d) => <LedgerBody d={d} onCharge={setLineage} />}</Loading>
      </div>
      {lineage && <LineageDrawer chargeKey={lineage} onClose={() => setLineage(null)} />}
    </>
  );
}

function LedgerBody({ d, onCharge }: { d: InvoiceLedger; onCharge: (key: string) => void }) {
  const { t } = useCopy();
  const tot = d.totals;
  const has = (k: keyof Group) => d.groups.some((g) => k in g);
  const allocated = d.by_entity.reduce((a, e) => a + num(e.AllocatedCost), 0);
  return (
    <>
      <div className="ledger-kpis">
        <Kpi label="Invoiced" value={money(tot.invoiced, 2)} />
        <Kpi label="Cost rows, billed" value={money(tot.billed_rows, 2)} sub={`${tot.charge_lines.toLocaleString("en-US")} charges`} />
        <Kpi label="Cost rows, effective" value={money(tot.effective_rows, 2)} />
        <Kpi label="Allocated" value={money(tot.allocated, 2)} tone={Math.abs(tot.allocated - tot.effective_rows) <= 0.01 ? "good" : "bad"} />
        <Kpi label="Difference" value={money(tot.difference, 2)} tone={Math.abs(tot.difference) <= 1 ? "good" : ""} />
      </div>

      <div className="ledger-section">
        <h3>{t("reconciliation.where")}</h3>
        {d.groups.length === 0 ? <div className="muted small">No cost rows carry this invoice line.</div> : (
          <div className="table-wrap">
            <table>
              <thead><tr>
                <th>{t("reconciliation.columns.entity")}</th>
                {has("FunctionCode") && <th>{t("reconciliation.columns.function")}</th>}
                <th>{t("reconciliation.columns.account")}</th><th>{t("reconciliation.columns.title")}</th><th>Class</th>
                {has("RuleId") && <th>{t("reconciliation.columns.rule")}</th>}
                {has("Citation") && <th>{t("reconciliation.columns.citation")}</th>}
                {has("BillingClass") && <th>{t("reconciliation.columns.billing")}</th>}
                <th>Exception</th><th className="num">Lines</th><th className="num">Allocated</th>
              </tr></thead>
              <tbody>
                {d.groups.map((g, i) => (
                  <tr key={i}>
                    <td>{g.LegalEntity}</td>
                    {has("FunctionCode") && <td>{str(g.FunctionCode)}</td>}
                    <td className="id">{str(g.GlAccount)}</td>
                    <td className="small">{str(g.GlTitle)}</td>
                    <td className="small">{str(g.CostClass)}</td>
                    {has("RuleId") && <td className="id">{str(g.RuleId)}{g.PolicyFlag ? <span className="small muted"> · {g.PolicyFlag}</span> : null}</td>}
                    {has("Citation") && <td className="ledger-cite">{str(g.Citation)}</td>}
                    {has("BillingClass") && <td className="id">{str(g.BillingClass)}</td>}
                    <td>{g.Exception ? <StatusBadge s={g.Exception} /> : ""}</td>
                    <td className="num">{num(g.Lines).toLocaleString("en-US")}</td>
                    <td className="num">{money(g.AllocatedCost, 2)}</td>
                  </tr>
                ))}
                <tr className="total">
                  <td colSpan={3 + (has("FunctionCode") ? 1 : 0)}>Total</td><td /><td />
                  {has("RuleId") && <td />}{has("Citation") && <td />}{has("BillingClass") && <td />}
                  <td className="num">{d.groups.reduce((a, g) => a + num(g.Lines), 0).toLocaleString("en-US")}</td>
                  <td className="num">{money(tot.allocated, 2)}</td>
                </tr>
              </tbody>
            </table>
          </div>
        )}
      </div>

      {d.by_entity.length > 0 && (
        <div className="ledger-section">
          <h3>{t("reconciliation.byEntity")}</h3>
          <RecordTable rows={d.by_entity.map((e) => ({ [t("reconciliation.columns.entity") || "Entity"]: e.LegalEntity, AllocatedCost: e.AllocatedCost,
            Share: allocated ? `${(100 * num(e.AllocatedCost) / allocated).toFixed(1)}%` : "—" }))} />
        </div>
      )}

      <div className="ledger-section">
        <h3>{t("reconciliation.items")}</h3>
        {d.residuals.length === 0 ? <div className="muted small">{t("reconciliation.noItems")}</div> : (
          <RecordTable rows={d.residuals.map((r) => ({ Item: r.Item, Amount: r.Amount, Status: r.Explained ? "explained" : "open",
            Treatment: r.Treatment, ExpectedResolution: r.ExpectedResolution }))} />
        )}
      </div>

      {d.charges.length > 0 && (
        <div className="ledger-section">
          <h3>{t("reconciliation.charges")}</h3>
          <div className="table-wrap">
            <table className="ledger-click">
              <thead><tr><th>Day</th><th>Service</th><th>Item</th><th>Cost center</th><th className="num">Billed</th><th className="num">Effective</th></tr></thead>
              <tbody>
                {d.charges.map((c) => (
                  <tr key={c.ChargeKey} onClick={() => onCharge(c.ChargeKey)} title="Open the charge lineage">
                    <td className="id">{c.Day}</td><td>{c.Service}</td><td className="small">{str(c.Item)}</td><td className="small">{c.CostCenter}</td>
                    <td className="num">{money(c.Billed, 2)}</td><td className="num">{money(c.Effective, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <Formula>{d.formula}</Formula>
    </>
  );
}

// ------------------------------------------------------------------------------------------------ tag compliance
type Suspense = { amount: number; share_pct: number | null; usage: number; lines: number; owner: string; oldest_days: number | null;
  accounts: { SubAccountId: string; Account: string; Owner: string; Amount: number; Lines: number; FirstSeen: string; AgeDays: number | null }[] };
type TagData = {
  available: boolean; period?: PeriodInfo & { month?: string }; months?: string[]; keys?: Row[]; by_entity?: Row[]; by_business_unit?: Row[];
  cannot_be_tagged?: { amount: number; by_category: Row[] }; violations?: Row[]; suspense?: Suspense; cost_centers?: string[]; formula?: string;
};
export type RuleDraft = { Name: string; MatchColumn: string; MatchValue: string; CostCenter: string; EffectiveFrom: string; Account?: string };

const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;

/** Tag compliance for the selected period (or a billing month): coverage by required tag, company and business unit,
 *  spend that cannot be tagged, violations, and the suspense with its owner and age. Renders nothing for a tenant
 *  without a required-tag policy. */
export function TagCompliancePanel() {
  const { t } = useCopy();
  const { me, period, version } = useMe();
  const asked = useSearchParams().get("month") ?? "";
  const [month, setMonth] = useState(MONTH.test(asked) ? asked : "");
  const [draft, setDraft] = useState<RuleDraft | null>(null);
  const state = useApi<TagData>(month ? `/tag-compliance?month=${month}` : `/tag-compliance?range=${period}`, version);
  const d = state.data;
  if (!d || !d.available || !d.suspense || !d.cannot_be_tagged) return null;
  const s = d.suspense;
  const keys = d.keys ?? [];
  const invalid = keys.filter((k) => num(k.InvalidSpend) > 0);
  const bu = d.by_business_unit ?? [];
  const taggable = bu.reduce((a, r) => a + num(r.TaggableSpend), 0);
  const valid = bu.reduce((a, r) => a + num(r.ValidSpend), 0);
  const canSubmit = me.actions.includes("driver.submit");
  const first = (d.months ?? [])[0];
  return (
    <div className="stack tag-panel">
      <div className="card flush">
        <div className="card-head">
          <h2>{t("allocation.tags.title")} <span className="small muted">· {d.period?.label}</span></h2>
          <select value={month} onChange={(e) => setMonth(e.target.value)} aria-label="Billing month">
            <option value="">{`Selected period (${PERIODS.find((p) => p.key === period)?.label ?? period})`}</option>
            {(d.months ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
      </div>
      <div className="grid g4">
        <Kpi label={t("allocation.tags.suspense")} value={money(s.amount, 2)} tone={s.amount > 0 ? "bad" : "good"}
          sub={t("allocation.tags.suspenseSub", { share: s.share_pct ?? "—", owner: s.owner, days: s.oldest_days ?? 0 })} />
        <Kpi label={t("allocation.tags.invalid")} value={money(invalid.reduce((a, k) => a + num(k.InvalidSpend), 0), 2)}
          sub={invalid.map((k) => `${k.TagKey} ${money(k.InvalidSpend)}`).join(" · ") || "None"} />
        <Kpi label={t("allocation.tags.cannot")} value={money(d.cannot_be_tagged.amount, 2)} sub={t("allocation.tags.cannotSub")} />
        <Kpi label={t("allocation.tags.covered")} value={taggable ? `${(100 * valid / taggable).toFixed(1)}%` : "—"}
          sub={t("allocation.tags.coveredSub")} tone={taggable && valid / taggable >= 0.99 ? "good" : ""} />
      </div>
      <div className="grid g2w">
        <div className="card flush"><h2>{t("allocation.tags.byKey")}</h2>
          <RecordTable rows={keys} columns={["TagKey", "Requirement", "TaggableSpend", "ValidSpend", "UntaggedSpend", "InvalidSpend", "CoveragePct", "Status"]} /></div>
        <div className="card flush"><h2>{t("allocation.tags.cannotTitle")}</h2>
          <RecordTable rows={d.cannot_be_tagged.by_category} total={{ Category: "Total", Amount: d.cannot_be_tagged.amount }} /></div>
      </div>
      <div className="grid g2">
        <div className="card flush"><h2>{t("allocation.tags.byEntity")}</h2><RecordTable rows={d.by_entity ?? []} /></div>
        <div className="card flush"><h2>{t("allocation.tags.byUnit")}</h2><RecordTable rows={bu} /></div>
      </div>
      <div className="card flush">
        <h2>{t("allocation.tags.suspenseTitle")}</h2>
        {s.accounts.length === 0 ? <div className="muted small" style={{ padding: 12 }}>No spend in suspense for this period.</div> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Account</th><th>Owner</th><th className="num">Amount</th><th className="num">Lines</th><th>First seen</th>
                <th className="num">Age (days)</th>{canSubmit && <th />}</tr></thead>
              <tbody>
                {s.accounts.map((a) => (
                  <tr key={a.SubAccountId}>
                    <td>{a.Account} <span className="small muted mono">{a.SubAccountId}</span></td><td>{a.Owner}</td>
                    <td className="num">{money(a.Amount, 2)}</td><td className="num">{a.Lines.toLocaleString("en-US")}</td>
                    <td className="id">{a.FirstSeen}</td><td className="num">{str(a.AgeDays)}</td>
                    {canSubmit && <td><button className="btn" onClick={() => setDraft({ Name: "", Account: a.Account, MatchColumn: "SubAccountId",
                      MatchValue: a.SubAccountId, CostCenter: "", EffectiveFrom: first ? `${first}-01` : "" })}>{t("allocation.tags.propose")}</button></td>}
                  </tr>
                ))}
                <tr className="total"><td>Total</td><td>{s.owner}</td><td className="num">{money(s.amount, 2)}</td>
                  <td className="num">{s.lines.toLocaleString("en-US")}</td><td /><td className="num">{str(s.oldest_days)}</td>{canSubmit && <td />}</tr>
              </tbody>
            </table>
          </div>
        )}
      </div>
      <RuleProposals centers={d.cost_centers ?? []} firstMonth={first} draft={draft} setDraft={setDraft} />
      <div className="card flush"><h2>{t("allocation.tags.violations")}</h2>
        <RecordTable rows={d.violations ?? []} max={60}
          columns={["Account", "Owner", "TagKey", "Issue", "RawValue", "Spend", "FirstSeen", "AgeDays", "Resolution"]} /></div>
      {d.formula && <Formula>{d.formula}</Formula>}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ pooling-rule proposals
type Proposal = { ProposalId: string; Name: string; MatchColumn: string; MatchValue: string; CostCenter: string; EffectiveFrom: string;
  Status: string; SubmittedBy: string; SubmittedRole?: string; SubmittedAt: string; DecidedBy: string | null; DecidedRole?: string | null;
  DecidedAt: string | null; RuleId: string | null };
const MATCH = ["SubAccountId", "ServiceName", "ChargeCategory", "x_WorkspaceName", "ServiceProviderName"];

/** Maker-checker for pooling rules: a submitter proposes, someone else approves or returns; approval restates the
 *  allocation from the effective date. */
export function RuleProposals({ centers, firstMonth, draft, setDraft }: {
  centers: string[]; firstMonth?: string; draft: RuleDraft | null; setDraft: (d: RuleDraft | null) => void;
}) {
  const { t } = useCopy();
  const { me, version, refresh } = useMe();
  const [local, setLocal] = useState(0);
  const state = useApi<Proposal[]>("/allocation/rule-proposals", version * 1000 + local);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const canSubmit = me.actions.includes("driver.submit");
  const canApprove = me.actions.includes("rule.approve");
  const blank: RuleDraft = { Name: "", MatchColumn: "SubAccountId", MatchValue: "", CostCenter: "", EffectiveFrom: firstMonth ? `${firstMonth}-01` : "" };
  const f = draft ?? blank;
  const set = (k: keyof RuleDraft, v: string) => setDraft({ ...f, [k]: v });

  async function submit() {
    setBusy("submit");
    setMsg(null);
    try {
      const r = await post<{ proposal: Proposal; ledger: { Seq: number } }>("/allocation/rule-proposals", {
        MatchColumn: f.MatchColumn, MatchValue: f.MatchValue.trim(), CostCenter: f.CostCenter, EffectiveFrom: f.EffectiveFrom || undefined,
        Name: f.Name.trim() || (f.Account ? `${f.Account} to ${f.CostCenter}` : undefined) });
      setMsg(`${r.proposal.ProposalId} submitted for approval (decision ledger entry ${r.ledger.Seq}).`);
      setDraft(null);
      setLocal((x) => x + 1);
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }
  async function decide(p: Proposal, action: "approve" | "return") {
    setBusy(p.ProposalId + action);
    setMsg(null);
    try {
      const r = await post<{ proposal: Proposal; reallocated: Record<string, number>; ledger: { Seq: number } }>(
        `/allocation/rule-proposals/${p.ProposalId}/decision`, { action });
      const periods = Object.keys(r.reallocated ?? {});
      setMsg(action === "approve"
        ? `${p.ProposalId} approved as rule ${r.proposal.RuleId}; allocation restated for ${periods.join(", ") || "no periods"} (decision ledger entry ${r.ledger.Seq}).`
        : `${p.ProposalId} returned (decision ledger entry ${r.ledger.Seq}).`);
      setLocal((x) => x + 1);
      if (action === "approve") refresh();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  }
  return (
    <div className="card flush">
      <div className="card-head"><h2>{t("allocation.proposals.title")}</h2></div>
      <div className="rule-note">{t("allocation.proposals.note")}</div>
      {msg && <div className="notice">{msg}</div>}
      {canSubmit && (
        <div className="rule-form">
          <label>Match column
            <select value={f.MatchColumn} onChange={(e) => set("MatchColumn", e.target.value)}>{MATCH.map((m) => <option key={m}>{m}</option>)}</select>
          </label>
          <label>Match value<input value={f.MatchValue} onChange={(e) => set("MatchValue", e.target.value)} /></label>
          <label>Cost center
            <select value={f.CostCenter} onChange={(e) => set("CostCenter", e.target.value)}>
              <option value="">Choose…</option>{centers.map((c) => <option key={c}>{c}</option>)}
            </select>
          </label>
          <label>Effective from<input type="date" value={f.EffectiveFrom} onChange={(e) => set("EffectiveFrom", e.target.value)} /></label>
          <label>Name<input value={f.Name} onChange={(e) => set("Name", e.target.value)} placeholder={f.Account && f.CostCenter ? `${f.Account} to ${f.CostCenter}` : "Optional"} /></label>
          <button className="btn primary" disabled={!!busy || !f.MatchValue || !f.CostCenter} onClick={submit}>
            {busy === "submit" ? "Submitting…" : t("allocation.proposals.submit")}
          </button>
        </div>
      )}
      <Loading state={state}>
        {(list) => list.length === 0 ? <div className="muted small" style={{ padding: "0 16px 14px" }}>No proposals yet.</div> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Proposal</th><th>Name</th><th>Match</th><th>Cost center</th><th>Effective from</th><th>Status</th>
                <th>Submitted</th><th>Decided</th><th>Rule</th>{canApprove && <th />}</tr></thead>
              <tbody>
                {list.map((p) => (
                  <tr key={p.ProposalId}>
                    <td className="id">{p.ProposalId}</td><td>{p.Name}</td>
                    <td className="small"><span className="mono">{p.MatchColumn}</span> = {p.MatchValue}</td>
                    <td>{p.CostCenter}</td><td className="id">{p.EffectiveFrom}</td><td><StatusBadge s={p.Status} /></td>
                    <td className="small">{p.SubmittedRole ?? p.SubmittedBy}<br /><span className="muted">{str(p.SubmittedAt).replace("T", " ")}</span></td>
                    <td className="small">{p.DecidedAt ? <>{p.DecidedRole ?? p.DecidedBy}<br /><span className="muted">{p.DecidedAt.replace("T", " ")}</span></> : "—"}</td>
                    <td className="id">{str(p.RuleId)}</td>
                    {canApprove && <td>{p.Status === "submitted" && <>
                      <button className="btn primary" disabled={!!busy} onClick={() => decide(p, "approve")}>{busy === p.ProposalId + "approve" ? "Approving…" : "Approve"}</button>
                      <button className="btn" disabled={!!busy} onClick={() => decide(p, "return")}>Return</button>
                    </>}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Loading>
    </div>
  );
}
