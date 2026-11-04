"use client";
// Regulatory ledger tabs of the tax module page (components/views/tax.tsx), for tenants whose ledger carries rule columns:
// service-company billing by class, the ledger by account with its transaction listing, the rule register and the
// capital path. Each endpoint answers {available: false} for other tenants, and these views then render nothing.
// Every label comes from the tenant copy (tax.*; column headers tax.cols.<field>).
import "@/app/rates.css";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Fragment, useState, type ReactNode } from "react";
import { useCopy } from "@/components/copy";
import { useMe } from "@/components/shell";
import { Formula, Kpi, Loading, StatusBadge } from "@/components/ui";
import { LineageDrawer } from "@/components/usecases";
import { download, useApi, type Row } from "@/lib/api";
import { isMoneyCol, label, money, num } from "@/lib/format";

type Unavailable = { available: false };
const MONEY = /additions|plant$|^depreciation|^amount|^direct$|^indirect$|^total$/i;
const isMoney = (c: string) => isMoneyCol(c) || MONEY.test(c);
const n = (v: unknown) => Number(v ?? 0);
const day = (v: unknown) => (v == null ? "—" : String(v).replace("T", " ").slice(0, 10));

/** Header text for a field: tax.cols.<field> from the copy, else the field name split into words. */
function useCols() {
  const { t } = useCopy();
  return (c: string) => t(`tax.cols.${c}`, undefined, label(c));
}

function cell(c: string, v: unknown): ReactNode {
  if (v == null || v === "") return "—";
  if (typeof v === "number") return /pct$/i.test(c) ? `${v.toFixed(1)}%` : isMoney(c) ? money(v, 2) : num(v);
  if (/status/i.test(c)) return <StatusBadge s={v} />;
  if (/date$|day$|At$/.test(c)) return day(v);
  return String(v);
}

/** Rows of objects with copy headers; `render` overrides a column's cell. */
function Table({ rows, cols, total, render, onRow, focus, max = 1000 }: {
  rows: Row[]; cols: string[]; total?: Row; render?: Record<string, (r: Row) => ReactNode>; onRow?: (r: Row) => void;
  focus?: (r: Row) => boolean; max?: number;
}) {
  const head = useCols();
  const numeric = cols.map((c) => rows.length > 0 && rows.every((r) => r[c] == null || typeof r[c] === "number"));
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{cols.map((c, i) => <th key={c} className={numeric[i] ? "num" : ""}>{head(c)}</th>)}</tr></thead>
        <tbody>
          {rows.slice(0, max).map((r, ri) => (
            <tr key={ri} className={focus?.(r) ? "focus" : ""} onClick={onRow ? () => onRow(r) : undefined} style={onRow ? { cursor: "pointer" } : undefined}>
              {cols.map((c, i) => <td key={c} className={numeric[i] ? "num" : /day$|date$|period|month/i.test(c) ? "id" : ""}>
                {render?.[c] ? render[c](r) : cell(c, r[c])}</td>)}
            </tr>
          ))}
          {total && <tr className="total">{cols.map((c, i) => <td key={c} className={numeric[i] ? "num" : ""}>{total[c] == null ? "" : cell(c, total[c])}</td>)}</tr>}
        </tbody>
      </table>
    </div>
  );
}

function PeriodSelect({ value, periods, onChange }: { value: string; periods: string[]; onChange: (p: string) => void }) {
  const { t } = useCopy();
  return (
    <label className="row rt-period">{t("tax.period")}
      <select value={value} onChange={(e) => onChange(e.target.value)}>{periods.map((p) => <option key={p} value={p}>{p}</option>)}</select>
    </label>
  );
}

function Unavailable() {
  const { t } = useCopy();
  return <p className="muted small">{t("tax.unavailable")}</p>;
}

// ------------------------------------------------------------------------------------------------ service company billing
type Billing = {
  year: number; period: string | null; periods: string[]; closed_period: string; lines: Row[]; by_entity: Row[]; formula: string;
  billing_formula: string; direct_share_pct: number | null; factors: Row[];
  totals: { direct: number; indirect: number; billed: number; held: number; ledger_billed: number; difference: number; without_account: number };
};

/** Bill lines by receiving company, 457.1 direct and 457.2 by factor, for one billing period (default the latest closed). */
export function BillingView({ periods, initial }: { periods: string[]; initial: string }) {
  const params = useSearchParams();
  const { t } = useCopy();
  const [period, setPeriod] = useState(params.get("period") ?? initial);
  const [entity, setEntity] = useState<string | null>(null);
  const state = useApi<Billing>(period ? `/tax/recharge-schedule?year=${period.slice(0, 4)}&period=${period}` : null);
  return (
    <div className="stack">
      <PeriodSelect value={period} periods={periods} onChange={(p) => { setPeriod(p); setEntity(null); }} />
      <Loading state={state}>
        {(d) => {
          const tot = d.totals;
          const codes = Object.fromEntries(d.lines.map((l) => [String(l.RecipientEntity), l.EntityCode]));
          const companies = d.by_entity.filter((e) => n(e.Total) !== 0).map((e) => ({ ...e, EntityCode: codes[String(e.RecipientEntity)] }));
          const lines = entity ? d.lines.filter((l) => l.RecipientEntity === entity) : d.lines;
          const ties = Math.abs(tot.difference) <= Math.max(0.01, 0.005 * d.lines.length);
          return (
            <>
              <div className="grid g4">
                <Kpi label={t("tax.billing.billed")} value={money(tot.billed)} sub={t("tax.billing.held", { amount: money(tot.held) })} />
                <Kpi label={t("tax.billing.direct")} value={money(tot.direct)} />
                <Kpi label={t("tax.billing.indirect")} value={money(tot.indirect)} />
                <Kpi label={t("tax.billing.share")} value={d.direct_share_pct == null ? "—" : `${d.direct_share_pct.toFixed(1)}%`} />
              </div>
              <div className="card flush">
                <h2>{t("tax.billing.byCompany")}</h2>
                <Table rows={companies} cols={["RecipientEntity", "EntityCode", "Direct", "Indirect", "Total", "DirectSharePct"]}
                  total={{ RecipientEntity: t("tax.credit.total"), Direct: tot.direct, Indirect: tot.indirect, Total: tot.billed, DirectSharePct: d.direct_share_pct }}
                  onRow={(r) => setEntity(entity === r.RecipientEntity ? null : String(r.RecipientEntity))} focus={(r) => r.RecipientEntity === entity} />
              </div>
              <div className="card flush">
                <div className="card-head">
                  <h2>{entity ? t("tax.billing.linesFor", { entity }) : t("tax.billing.lines")}</h2>
                  {entity && <button className="btn sm" onClick={() => setEntity(null)}>{t("tax.billing.all")}</button>}
                </div>
                <Table rows={lines} cols={["RecipientEntity", "BillingClass", "Factor", "BasisOfAllocation", "Policy", "ReceivingAccount", "ChargeLines", "AmountRecharged", "Status"]} />
              </div>
              <p className={`small ${ties ? "" : "bad"}`}>
                {ties ? t("tax.billing.ties", { billed: money(tot.billed, 2) })
                  : t("tax.billing.differs", { ledger: money(tot.ledger_billed, 2), difference: money(tot.difference, 2) })}
                {tot.without_account > 0 && <> {t("tax.billing.noAccount", { amount: money(tot.without_account, 2) })}</>}
              </p>
              <div className="card flush">
                <h2>{t("tax.billing.factors")}</h2>
                <Table rows={d.factors} cols={["FactorCode", "DriverName", "BillingClass", "Refresh", "UsedFor", "Status", "AmountBilled"]} />
              </div>
              <Formula>{d.billing_formula}</Formula>
            </>
          );
        }}
      </Loading>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ ledger by account
type Ledger = { available: true; period: string; periods: string[]; accounts: Row[]; by_entity_function: Row[]; total: number;
  listing: boolean; formula: string } | Unavailable;
type Listing = { available: true; account: string; title: string | null; period: string; entity: string | null; entities: string[];
  count: number; total: number; rows: Row[]; truncated: boolean; formula: string } | Unavailable;

const FUNCTIONS = ["T", "D", "CUST", "AG", "GENERAL", "NONUTIL"];

export function LedgerTab() {
  const params = useSearchParams();
  const router = useRouter();
  const { t } = useCopy();
  const account = params.get("account");
  const period = params.get("period") ?? "";
  const go = (p: string, a: string | null) => router.replace(`/tax?tab=ledger${p ? `&period=${p}` : ""}${a ? `&account=${encodeURIComponent(a)}` : ""}`);
  const state = useApi<Ledger>(`/tax/ledger${period ? `?period=${period}` : ""}`);
  return (
    <Loading state={state}>
      {(d) => !d.available ? <Unavailable /> : (
        <div className="stack">
          <PeriodSelect value={d.period} periods={d.periods} onChange={(p) => go(p, account)} />
          <LedgerSummary d={d} account={account} onAccount={(a) => go(d.period, a === account ? null : a)} />
          {d.listing && (account
            ? <AccountListing key={`${account}|${d.period}`} account={account} period={d.period} />
            : <p className="muted small">{t("tax.ledger.pick")}</p>)}
          <Formula>{d.formula}</Formula>
        </div>
      )}
    </Loading>
  );
}

function LedgerSummary({ d, account, onAccount }: { d: Extract<Ledger, { available: true }>; account: string | null; onAccount: (a: string) => void }) {
  const { t } = useCopy();
  const head = useCols();
  const policy = d.accounts.filter((a) => a.PolicyFlag === "POLICY").reduce((s, a) => s + n(a.AllocatedCost), 0);
  const regulation = d.accounts.filter((a) => a.PolicyFlag === "Regulation").reduce((s, a) => s + n(a.AllocatedCost), 0);
  const entities = [...new Set(d.by_entity_function.map((r) => String(r.LegalEntity)))];
  const fns = [...FUNCTIONS.filter((f) => d.by_entity_function.some((r) => r.FunctionCode === f)),
    ...new Set(d.by_entity_function.map((r) => (r.FunctionCode == null ? "" : String(r.FunctionCode))).filter((f) => !FUNCTIONS.includes(f)))];
  const amt = (e: string, f: string) => d.by_entity_function.filter((r) => r.LegalEntity === e && (r.FunctionCode ?? "") === f).reduce((s, r) => s + n(r.AllocatedCost), 0);
  return (
    <>
      <div className="grid g4">
        <Kpi label={t("tax.ledger.total")} value={money(d.total)} />
        <Kpi label={t("tax.ledger.accounts")} value={new Set(d.accounts.map((a) => a.GlAccount)).size} />
        <Kpi label={t("tax.ledger.regulation")} value={money(regulation)} />
        <Kpi label={t("tax.ledger.policy")} value={money(policy)} />
      </div>
      <div className="card flush">
        <h2>{t("tax.ledger.byAccount")}</h2>
        <Table rows={d.accounts} cols={["GlAccount", "GlTitle", "FunctionCode", "CostClass", "RuleId", "PolicyFlag", "Lines", "AllocatedCost"]}
          total={{ GlAccount: t("tax.credit.total"), AllocatedCost: d.total }} focus={(r) => r.GlAccount === account}
          render={{
            GlAccount: (r) => d.listing
              ? <a href="#" className="rt-acct" onClick={(e) => { e.preventDefault(); onAccount(String(r.GlAccount)); }}>{String(r.GlAccount)}</a>
              : <b>{String(r.GlAccount)}</b>,
            PolicyFlag: (r) => <Basis v={r.PolicyFlag} />,
          }} />
      </div>
      <div className="card flush">
        <h2>{t("tax.ledger.byEntityFunction")}</h2>
        <div className="table-wrap">
          <table>
            <thead><tr><th>{head("LegalEntity")}</th>{fns.map((f) => <th key={f} className="num">{f || "—"}</th>)}<th className="num">{t("tax.credit.total")}</th></tr></thead>
            <tbody>
              {entities.map((e) => (
                <tr key={e}><td>{e}</td>{fns.map((f) => <td key={f} className="num">{amt(e, f) ? money(amt(e, f)) : "—"}</td>)}
                  <td className="num"><b>{money(fns.reduce((s, f) => s + amt(e, f), 0))}</b></td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function Basis({ v }: { v: unknown }) {
  const { t } = useCopy();
  if (v == null) return <>—</>;
  return v === "POLICY" ? <span className="badge b-warn">{t("tax.rules.policy")}</span> : <span className="badge b-info">{String(v)}</span>;
}

/** Every ledger line booked to one account in the period: vendor, description, amount, invoice (opens the invoice drawer). */
function AccountListing({ account, period }: { account: string; period: string }) {
  const { me } = useMe();
  const { t } = useCopy();
  const [entity, setEntity] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [lineage, setLineage] = useState<string | null>(null);
  const q = `account=${encodeURIComponent(account)}&period=${period}${entity ? `&entity=${encodeURIComponent(entity)}` : ""}`;
  const state = useApi<Listing>(`/tax/ledger/listing?${q}`);
  const shown = 500;
  return (
    <Loading state={state}>
      {(l) => !l.available ? null : (
        <div className="card flush" id="listing">
          <div className="card-head">
            <h2>{t("tax.ledger.listing", { account: l.account, title: l.title ?? "" })} <span className="small muted">· {l.period} · {money(l.total, 2)}</span></h2>
            <div className="row">
              <select value={entity} onChange={(e) => setEntity(e.target.value)}>
                <option value="">{t("tax.ledger.all")}</option>
                {l.entities.map((e) => <option key={e} value={e}>{e}</option>)}
              </select>
              {me.actions.includes("exhibit.export") && (
                <button className="btn primary sm" onClick={() => download(`/tax/ledger/listing.csv?${q}`, `${l.account}-${l.period}-transactions.csv`)
                  .then(() => setMsg(t("tax.ledger.exported"))).catch((e: Error) => setMsg(e.message))}>{t("tax.ledger.export")}</button>
              )}
            </div>
          </div>
          {msg && <div className="notice rt-msg">{msg}</div>}
          <Table rows={l.rows} max={shown}
            cols={["ChargeDay", "LegalEntity", "Jurisdiction", "RuleId", "Vendor", "Description", "Application", "CostCenter", "InvoiceId", "AllocatedCost"]}
            total={{ ChargeDay: t("tax.credit.total"), AllocatedCost: l.total }}
            render={{
              InvoiceId: (r) => r.InvoiceDetailId
                ? <Link href={`/reconciliation?detail=${encodeURIComponent(String(r.InvoiceDetailId))}`}>{String(r.InvoiceId ?? r.InvoiceDetailId)}</Link>
                : cell("InvoiceId", r.InvoiceId),
              AllocatedCost: (r) => <span className="rt-amt">{money(r.AllocatedCost, 2)}
                {r.ChargeKey != null && <button className="btn sm" title={t("tax.ledger.lineage")} onClick={() => setLineage(String(r.ChargeKey))}>{t("tax.ledger.lineage")}</button>}</span>,
            }} />
          {(l.count > shown) && <div className="muted small rt-msg">{t("tax.ledger.shown", { shown, count: l.count })}</div>}
          <div className="rt-pad"><Formula>{l.formula}</Formula></div>
          {lineage && <LineageDrawer chargeKey={lineage} onClose={() => setLineage(null)} />}
        </div>
      )}
    </Loading>
  );
}

/** Audit files tab: export one account's transaction listing for a period (hidden when the ledger has no listing). */
export function ListingExport() {
  const { me } = useMe();
  const { t } = useCopy();
  const [period, setPeriod] = useState("");
  const [account, setAccount] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const state = useApi<Ledger>(`/tax/ledger${period ? `?period=${period}` : ""}`);
  const d = state.data;
  if (!d || !d.available || !d.listing) return null;
  const accounts = [...new Map(d.accounts.map((a) => [String(a.GlAccount), String(a.GlTitle ?? "")])).entries()];
  const acct = account || (accounts.find(([a]) => a === "566") ?? accounts[0])?.[0] || "";
  return (
    <div className="card">
      <h2>{t("tax.ledger.exportTitle")}</h2>
      <div className="row">
        <PeriodSelect value={d.period} periods={d.periods} onChange={(p) => { setPeriod(p); setMsg(null); }} />
        <select value={acct} onChange={(e) => { setAccount(e.target.value); setMsg(null); }}>
          {accounts.map(([a, title]) => <option key={a} value={a}>{a} {title}</option>)}
        </select>
        <button className="btn primary" disabled={!acct || !me.actions.includes("exhibit.export")} onClick={() =>
          download(`/tax/ledger/listing.csv?account=${encodeURIComponent(acct)}&period=${d.period}`, `${acct}-${d.period}-transactions.csv`)
            .then(() => setMsg(t("tax.ledger.exported"))).catch((e: Error) => setMsg(e.message))}>{t("tax.ledger.export")}</button>
        <Link href={`/tax?tab=ledger&period=${d.period}&account=${encodeURIComponent(acct)}`}>{t("tax.ledger.open")}</Link>
      </div>
      {msg && <div className="notice rt-msg">{msg}</div>}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ rule register
type Register = { available: true; period: string; periods: string[]; usecase: string; rules: Row[]; awaiting_signoff: string[]; total: number;
  ledger_total: number; difference: number; blocked_changes: Row[]; formula: string } | Unavailable;

export function RulesTab() {
  const params = useSearchParams();
  const { t, get } = useCopy();
  const [period, setPeriod] = useState(params.get("period") ?? "");
  const state = useApi<Register>(`/tax/rule-register${period ? `?period=${period}` : ""}`);
  const accounts = get<[string, string][]>("tax.rules.accounts") ?? [];
  return (
    <Loading state={state}>
      {(d) => !d.available ? <Unavailable /> : (
        <div className="stack">
          <PeriodSelect value={d.period} periods={d.periods} onChange={setPeriod} />
          <div className="grid g4">
            <Kpi label={t("tax.rules.count")} value={d.rules.length} />
            <Kpi label={t("tax.rules.awaiting")} value={d.awaiting_signoff.length} sub={d.awaiting_signoff.join(", ")} tone={d.awaiting_signoff.length ? "bad" : "good"} />
            <Kpi label={t("tax.rules.placed")} value={money(d.total)} />
            <Kpi label={t("tax.ledger.total")} value={money(d.ledger_total)} />
          </div>
          <p className={`small ${Math.abs(d.difference) <= 0.1 ? "" : "bad"}`}>
            {Math.abs(d.difference) <= 0.1 ? t("tax.rules.ties", { total: money(d.ledger_total, 2) })
              : t("tax.rules.differs", { total: money(d.ledger_total, 2), difference: money(d.difference, 2) })}
          </p>
          <div className="card flush">
            <h2>{t("tax.rules.register")} <span className="small muted">({d.usecase})</span></h2>
            <RuleTable rules={d.rules} accounts={accounts} total={d.total} />
          </div>
          {d.blocked_changes.length > 0 && (
            <div className="card flush">
              <h2>{t("tax.rules.blocked")}</h2>
              <Table rows={d.blocked_changes} cols={["ChangeId", "RuleId", "ProposedAccount", "Description", "ProposedBy", "Status", "BlockedBy", "ExceptionCode"]}
                render={{
                  ChangeId: (r) => <><b>{String(r.ChangeId)}</b><div className="small muted">{day(r.ProposedAt)}</div></>,
                  Description: (r) => <span className="small">{String(r.Description ?? "")}<div className="muted">{String(r.MatchColumn ?? "")} = {String(r.MatchValue ?? "")}</div></span>,
                  Status: (r) => <span className="badge b-bad">{String(r.Status)}</span>,
                }} />
            </div>
          )}
          <Formula>{d.formula}</Formula>
        </div>
      )}
    </Loading>
  );
}

function RuleTable({ rules, accounts, total }: { rules: Row[]; accounts: [string, string][]; total: number }) {
  const head = useCols();
  const { t } = useCopy();
  return (
    <div className="table-wrap rt-rules">
      <table>
        <thead>
          <tr><th>{head("RuleId")}</th>{accounts.map(([k, l]) => <th key={k}>{l}</th>)}<th>{head("Condition")}</th><th>{head("Citation")}</th>
            <th>{head("Basis")}</th><th>{head("Status")}</th><th className="num">{head("Lines")}</th><th className="num">{head("AllocatedCost")}</th></tr>
        </thead>
        <tbody>
          {rules.map((r) => {
            const awaiting = /^awaiting/i.test(String(r.Status ?? ""));
            return (
              <tr key={String(r.RuleId)}>
                <td className="rt-rule"><b>{String(r.RuleId)}</b> <span className="muted small">{head("EvaluationStep")} {String(r.EvaluationStep ?? "")}</span>
                  <div className="small">{String(r.ChargePattern ?? "")}</div></td>
                {accounts.map(([k]) => <td key={k} className="id">{r[k] == null ? "—" : String(r[k])}</td>)}
                <td className="small">{String(r.Condition ?? "")}</td>
                <td className="small">{String(r.Citation ?? "")}</td>
                <td><Basis v={r.Basis} /></td>
                <td><span className={`badge ${awaiting ? "b-warn" : "b-good"}`}>{String(r.Status ?? "")}</span>
                  {r.SignedOffBy != null && <div className="small muted">{String(r.SignedOffBy)} · {day(r.SignedOffAt)}</div>}</td>
                <td className="num">{num(r.AllocatedLines ?? 0)}</td>
                <td className="num">{money(r.AllocatedCost, 2)}</td>
              </tr>
            );
          })}
          <tr className="total"><td>{t("tax.credit.total")}</td>{accounts.map(([k]) => <td key={k} />)}<td /><td /><td /><td />
            <td className="num">{num(rules.reduce((s, r) => s + n(r.AllocatedLines), 0))}</td><td className="num">{money(total, 2)}</td></tr>
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ capital path
type Held = { ExceptionCode: string; Lines: number; Amount: number; Items: Row[] };
type Capital = { available: true; usecase: string; as_of: string; work_orders: Row[]; schedule: Row[]; held: Held[]; formula: string } | Unavailable;

export function CapitalTab() {
  const params = useSearchParams();
  const router = useRouter();
  const { t } = useCopy();
  const state = useApi<Capital>("/tax/capital-path");
  const wo = params.get("wo");
  const [mod, setMod] = useState<string | null>(null);
  return (
    <Loading state={state}>
      {(d) => {
        if (!d.available) return <Unavailable />;
        const cards = d.work_orders.filter((w) => w.WorkOrderId !== "(none)" && w.Module !== "-");
        const sel = cards.find((w) => w.WorkOrderId === wo && (mod == null || w.Module === mod)) ?? null;
        const sched = sel ? d.schedule.filter((s) => s.WorkOrderId === sel.WorkOrderId && s.Module === sel.Module) : [];
        return (
          <div className="stack">
            <div className="rt-cards">
              {cards.map((w) => (
                <WorkOrderCard key={`${w.WorkOrderId}|${w.Module}`} w={w} on={sel === w}
                  onClick={() => { setMod(String(w.Module)); router.replace(`/tax?tab=capital&wo=${encodeURIComponent(String(w.WorkOrderId))}`); }} />
              ))}
            </div>
            {sel ? (
              <div className="card flush" id="schedule">
                <h2>{t("tax.capital.schedule", { id: `${sel.WorkOrderId} · ${sel.Module}` })}</h2>
                <Table rows={sched} cols={["Month", "Cwip107Additions", "Cwip107Balance", "TransferredToPlant", "PlantAccount", "Depreciation403"]}
                  total={{ Month: t("tax.credit.total"), Cwip107Additions: sched.reduce((s, r) => s + n(r.Cwip107Additions), 0),
                    TransferredToPlant: sched.reduce((s, r) => s + n(r.TransferredToPlant), 0), Depreciation403: sched.reduce((s, r) => s + n(r.Depreciation403), 0) }} />
              </div>
            ) : <p className="muted small">{t("tax.capital.pick")}</p>}
            {d.held.length > 0 && (
              <div className="card flush">
                <h2>{t("tax.capital.held")}</h2>
                <HeldTable held={d.held} />
              </div>
            )}
            <Formula>{d.formula}</Formula>
          </div>
        );
      }}
    </Loading>
  );
}

function WorkOrderCard({ w, on, onClick }: { w: Row; on: boolean; onClick: () => void }) {
  const { t } = useCopy();
  const head = useCols();
  const inService = n(w.TransferredToPlant) > 0;
  const held = /^held/i.test(String(w.Status ?? ""));
  return (
    <button className={`card rt-wo${on ? " on" : ""}`} onClick={onClick}>
      <div className="rt-wo-id">{String(w.WorkOrderId)} <span className="muted">· {String(w.Module)}</span></div>
      <div className="small muted rt-wo-desc">{String(w.Description ?? "")}</div>
      <div className="rt-path">
        <span className="rt-node"><span className="small muted">{inService ? t("tax.capital.cwip") : t("tax.capital.building")}</span>
          <b>{money(inService ? w.TransferredToPlant : w.Cwip107Balance, 2)}</b></span>
        <span className="rt-arrow">→</span>
        <span className="rt-node"><span className="small muted">{head("PlantAccount")}</span>
          <b>{w.ReadyForUseDate ? t("tax.capital.plant", { account: String(w.TargetPlantAccount ?? "—"), date: day(w.ReadyForUseDate) })
            : String(w.TargetPlantAccount ?? "—")}</b>
          {!w.ReadyForUseDate && <span className="small muted">{t("tax.capital.open")}</span>}</span>
        <span className="rt-arrow">→</span>
        <span className="rt-node"><span className="small muted">{head("Depreciation403")}</span>
          <b>{inService ? t("tax.capital.depreciation", { amount: money(w.MonthlyDepreciation403, 2) }) : "—"}</b></span>
      </div>
      <div className="row rt-wo-foot">
        <span className={`badge ${held ? "b-bad" : inService ? "b-good" : "b-warn"}`}>{String(w.Status ?? "")}</span>
        {n(w.HeldOutOf107) > 0 && <span className="small bad">{money(w.HeldOutOf107, 2)}</span>}
      </div>
    </button>
  );
}

function HeldTable({ held }: { held: Held[] }) {
  const head = useCols();
  const { t } = useCopy();
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>{head("ExceptionCode")}</th><th>{head("Vendor")}</th><th>{head("Description")}</th><th>{head("CostCenter")}</th>
          <th>{head("WorkOrderId")}</th><th className="num">{head("Lines")}</th><th className="num">{head("Amount")}</th></tr></thead>
        <tbody>
          {held.map((h) => (
            <Fragment key={h.ExceptionCode}>
              <tr className="rt-group"><td colSpan={5}><span className="badge b-bad">{h.ExceptionCode}</span></td>
                <td className="num">{num(h.Lines)}</td><td className="num"><b>{money(h.Amount, 2)}</b></td></tr>
              {h.Items.map((i, k) => (
                <tr key={k}><td /><td>{String(i.Vendor ?? "—")}</td><td className="small">{String(i.Description ?? "—")}</td>
                  <td className="small">{String(i.CostCenter ?? "—")}</td><td className="id">{String(i.WorkOrderId ?? "—")}</td>
                  <td className="num" /><td className="num">{money(i.Amount, 2)}</td></tr>
              ))}
            </Fragment>
          ))}
          <tr className="total"><td colSpan={6}>{t("tax.credit.total")}</td><td className="num">{money(held.reduce((s, h) => s + h.Amount, 0), 2)}</td></tr>
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ allocation chain extras
/** Chain tab: dollars by rule and by billing class when the allocation chain carries them. */
export function ChainExtras({ rules, billing }: { rules?: Row[]; billing?: Row[] }) {
  const { t } = useCopy();
  if (!rules?.length && !billing?.length) return null;
  return (
    <div className="grid g2w">
      {rules && rules.length > 0 && (
        <div className="card flush"><h2>{t("tax.chain.byRule")}</h2>
          <Table rows={rules} cols={["RuleId", "Citation", "PolicyFlag", "Lines", "AllocatedCost"]}
            total={{ RuleId: t("tax.credit.total"), AllocatedCost: rules.reduce((s, r) => s + n(r.AllocatedCost), 0) }}
            render={{ PolicyFlag: (r) => <Basis v={r.PolicyFlag} />, Citation: (r) => <span className="small">{String(r.Citation ?? "—")}</span> }} />
        </div>
      )}
      {billing && billing.length > 0 && (
        <div className="card flush"><h2>{t("tax.chain.byBilling")}</h2>
          <Table rows={billing} cols={["BillingClass", "AllocatedCost"]}
            total={{ BillingClass: t("tax.credit.total"), AllocatedCost: billing.reduce((s, r) => s + n(r.AllocatedCost), 0) }} />
        </div>
      )}
    </div>
  );
}
