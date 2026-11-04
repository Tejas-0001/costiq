"use client";
import Link from "next/link";
import { useState } from "react";
import { BarChart } from "@/components/charts";
import { Gate, useMe, type PeriodInfo } from "@/components/shell";
import { Findings, LineageDrawer, ModuleUseCases, type Finding } from "@/components/usecases";
import { Formula, Head, Kpi, Loading, RecordTable, StatusBadge } from "@/components/ui";
import { useApi, type Row } from "@/lib/api";
import { money, pct } from "@/lib/format";

// ------------------------------------------------------------------------------------------------ explorer
type Explorer = { period: PeriodInfo | null; months: string[]; providers: string[]; by_provider: Row[]; by_cost_center: Row[]; by_service: Row[]; lines: Row[]; formula: string };

export function ExplorerView() {
  const { period } = useMe();
  const [f, setF] = useState({ month: "", provider: "", cost_center: "" });
  const [lineage, setLineage] = useState<string | null>(null);
  const q = new URLSearchParams([...Object.entries(f).filter(([, v]) => v), ["range", period]]).toString();
  const state = useApi<Explorer>(`/explorer?${q}`);
  return (
    <Gate module="explorer">
      <Head title="Spend Explorer" />
      <Loading state={state}>
        {(d) => (
          <div className="stack">
            <div className="card row">
              <select value={f.month} onChange={(e) => setF({ ...f, month: e.target.value })}><option value="">{d.period ? d.period.label : "All months"}</option>{d.months.map((m) => <option key={m}>{m}</option>)}</select>
              <select value={f.provider} onChange={(e) => setF({ ...f, provider: e.target.value })}><option value="">All providers</option>{d.providers.map((m) => <option key={m}>{m}</option>)}</select>
              <select value={f.cost_center} onChange={(e) => setF({ ...f, cost_center: e.target.value })}><option value="">All cost centers</option>
                {d.by_cost_center.map((r) => <option key={String(r.CostCenter)}>{String(r.CostCenter)}</option>)}</select>
              <button className="btn" onClick={() => setF({ month: "", provider: "", cost_center: "" })}>Reset</button>
            </div>
            <div className="grid g3">
              <div className="card"><h2>By provider</h2><BarChart horizontal height="short" labels={d.by_provider.map((r) => String(r.Provider))} datasets={[{ label: "Effective", data: d.by_provider.map((r) => Number(r.Effective)) }]} /></div>
              <div className="card"><h2>By cost center</h2><BarChart horizontal height="short" labels={d.by_cost_center.map((r) => String(r.CostCenter))} datasets={[{ label: "Effective", data: d.by_cost_center.map((r) => Number(r.Effective)), backgroundColor: "#454d53" }]} /></div>
              <div className="card"><h2>Top services</h2><BarChart horizontal height="short" labels={d.by_service.map((r) => String(r.Service))} datasets={[{ label: "Effective", data: d.by_service.map((r) => Number(r.Effective)), backgroundColor: "#0f6ec8" }]} /></div>
            </div>
            <div className="card flush">
              <h2>Largest charge lines</h2>
              <RecordTable rows={d.lines} columns={["Day", "Provider", "Service", "Item", "CostCenter", "Billed", "Effective"]} onRow={(r) => {
                const hit = d.lines.find((l) => l.Day === r.Day && l.Item === r.Item && l.Effective === r.Effective);
                if (hit) setLineage(String(hit.ChargeKey));
              }} />
            </div>
            <Formula>{d.formula}</Formula>
            <ModuleUseCases module="explorer" />
            {lineage && <LineageDrawer chargeKey={lineage} onClose={() => setLineage(null)} />}
          </div>
        )}
      </Loading>
    </Gate>
  );
}

// ------------------------------------------------------------------------------------------------ budgets, allocation, rates
export function BudgetsView() {
  const state = useApi<{ lines: Row[]; open_month: string; formula: string }>("/budgets");
  const [month, setMonth] = useState<string | null>(null);
  return (
    <Gate module="budgets">
      <Head title="Budgets & Forecasts" />
      <Loading state={state}>
        {(b) => {
          const months = [...new Set(b.lines.map((l) => String(l.Month)))].sort();
          const m = month ?? b.open_month;
          const lines = b.lines.filter((l) => l.Month === m);
          return (
            <div className="stack">
              <div className="row">{months.map((x) => <button key={x} className={`chip ${x === m ? "on" : ""}`} onClick={() => setMonth(x)}>{x}{x === b.open_month ? " (open)" : ""}</button>)}</div>
              <div className="card"><h2>Forecast and budget by cost center · {m}</h2>
                <BarChart labels={lines.map((l) => String(l.CostCenter))} datasets={[{ label: "Forecast", data: lines.map((l) => Number(l.Forecast)) },
                  { label: "Budget", data: lines.map((l) => Number(l.Budget)), backgroundColor: "#b8c1cb" }]} /></div>
              <div className="card flush"><RecordTable rows={lines.map((l) => ({ ...l, VariancePct: pct(l.VariancePct as number) }))}
                columns={["CostCenter", "Budget", "Actual", "Forecast", "Variance", "VariancePct", "Status"]} /></div>
              <Formula>{b.formula}</Formula>
              <ModuleUseCases module="budgets" />
            </div>
          );
        }}
      </Loading>
    </Gate>
  );
}

export function AllocationView() {
  const { period } = useMe();
  const state = useApi<{ rules: Row[]; by_method: Row[]; by_cost_center: Row[]; coverage: number; formula: string; period: PeriodInfo | null }>(`/allocation?range=${period}`);
  return (
    <Gate module="allocation">
      <Head title="Allocation & Chargeback">
        <Link href="/tax?tab=chain">Entity, jurisdiction and GL steps</Link>
      </Head>
      <Loading state={state}>
        {(a) => (
          <div className="stack">
            <div className="grid g3">
              <Kpi label="Allocation coverage" value={`${a.coverage ?? "—"}%`} tone={(a.coverage ?? 0) >= 99 ? "good" : "bad"} formula={a.formula} sub={a.period?.label} />
              <Kpi label="Approved rules" value={a.rules.length} />
              <Kpi label="Unallocated" value={money(a.by_method.find((m) => m.Method === "Unallocated")?.EffectiveCost ?? 0)} />
            </div>
            <div className="grid g2">
              <div className="card flush"><RecordTable rows={a.by_method} /></div>
              <div className="card"><h2>By cost center <span className="small muted">· {a.period?.long}</span></h2><BarChart horizontal labels={a.by_cost_center.map((r) => String(r.CostCenter))}
                datasets={[{ label: "Effective", data: a.by_cost_center.map((r) => Number(r.EffectiveCost)) }]} /></div>
            </div>
            <div className="card flush"><h2>Pooling rules</h2>
              <RecordTable rows={a.rules} columns={["RuleId", "Version", "Name", "Priority", "MatchColumn", "MatchValue", "CostCenter", "EffectiveFrom", "ApprovedBy"]} /></div>
            <ModuleUseCases module="allocation" />
          </div>
        )}
      </Loading>
    </Gate>
  );
}

export function RatesView() {
  return (
    <Gate module="rates">
      <Head title="Rates & Commitments" />
      <ModuleUseCases module="rates" />
    </Gate>
  );
}

export function AnomaliesView() {
  const { version, refresh } = useMe();
  const state = useApi<Finding[]>("/findings", version);
  return (
    <Gate module="anomalies">
      <Head title="Anomalies & Findings" />
      <Loading state={state}>
        {(f) => (
          <div className="stack">
            <div className="grid g3">
              <Kpi label="Incurred exposure" value={money(f.filter((x) => x.kind === "incurred").reduce((a, x) => a + x.exposure, 0))} tone="bad" />
              <Kpi label="Estimated opportunity" value={money(f.filter((x) => x.kind === "estimate").reduce((a, x) => a + x.exposure, 0))} />
              <Kpi label="Compliance findings" value={f.filter((x) => x.kind === "compliance").length} />
            </div>
            <Findings items={f} onDecided={refresh} />
            <ModuleUseCases module="anomalies" />
          </div>
        )}
      </Loading>
    </Gate>
  );
}

// ------------------------------------------------------------------------------------------------ reconciliation, governance
export function ReconciliationView() {
  const state = useApi<{ lines: Row[]; close: Row[]; formula: string }>("/reconciliation");
  return (
    <Gate module="reconciliation">
      <Head title="Reconciliation & Close" />
      <Loading state={state}>
        {(r) => (
          <div className="stack">
            <div className="row">{r.close.map((c) => <span key={String(c.Period) + String(c.Issuers)} className="pill">{String(c.Period)} <StatusBadge s={c.Readiness} /></span>)}</div>
            <div className="card flush"><RecordTable rows={[...r.lines].sort((a, b) => (a.Status === "exception" ? -1 : 0) - (b.Status === "exception" ? -1 : 0))}
              columns={["Period", "Issuer", "InvoiceId", "ChargeCategory", "IssueStatus", "Invoiced", "ChargeTotal", "Difference", "Status", "Reason"]} /></div>
            <Formula>{r.formula}</Formula>
            <ModuleUseCases module="reconciliation" />
          </div>
        )}
      </Loading>
    </Gate>
  );
}

export function GovernanceView() {
  const { version } = useMe();
  const state = useApi<{ controls: Row[]; ledger: Row[]; manifests: Row[]; ledger_verified: { verified: boolean; entries?: number; broken_at?: number } }>("/evidence", version);
  return (
    <Gate module="governance">
      <Head title="Governance & Evidence" />
      <Loading state={state}>
        {(g) => (
          <div className="stack">
            <div className="card flush"><h2>Controls</h2>
              <RecordTable rows={g.controls.map((c) => ({ ID: c.id, Control: c.control, Status: c.result, Evidence: c.evidence }))} /></div>
            <div className="card flush">
              <div className="card-head"><h2>Decision ledger</h2>
                <StatusBadge s={g.ledger_verified.verified ? `Chain verified (${g.ledger_verified.entries} entries)` : `Broken at ${g.ledger_verified.broken_at}`} /></div>
              <RecordTable rows={[...g.ledger].reverse().map((e) => ({ ...e, Hash: String(e.Hash).slice(0, 16) + "…" }))}
                columns={["Seq", "EntryAt", "Actor", "Persona", "Action", "Target", "Detail", "Hash"]} max={200} />
            </div>
            <div className="card flush"><h2>Ingestion manifests</h2>
              <RecordTable rows={g.manifests.map((m) => ({ ...m, Checks: (m.Checks as { status: string; check: string }[]).map((c) => `${c.status}: ${c.check}`).join("; ") }))}
                columns={["SourceSystem", "BillingPeriodStart", "Maturity", "RecordCount", "Digest", "Checks", "Status"]} /></div>
            <ModuleUseCases module="governance" />
          </div>
        )}
      </Loading>
    </Gate>
  );
}

// ------------------------------------------------------------------------------------------------ GxP & patient-data scorecard
type Area = { area: string; checks: number; attention: number; items: { id: string; title: string; reference: string; unit: string; checked: number;
  flagged: number; amount_affected: number; has_amount: boolean; status: string; error: string | null }[] };

export function ComplianceView() {
  const state = useApi<{ areas: Area[]; as_of: string; formula: string; disclaimer: string }>("/compliance/compliance");
  return (
    <Gate module="compliance">
      <Head title="GxP & Patient Data" />
      <Loading state={state}>
        {(c) => {
          const items = c.areas.flatMap((a) => a.items);
          return (
            <div className="stack">
              <p>{items.length} checks · <b className="bad">{items.filter((i) => i.status !== "compliant").length} need attention</b> ·{" "}
                {items.reduce((a, i) => a + i.flagged, 0)} items flagged · as of {c.as_of}</p>
              <table>
                <thead><tr><th>Area</th><th>Check</th><th>Reference</th><th className="num">Flagged</th><th className="num">Checked</th><th className="num">Amount</th><th>Status</th></tr></thead>
                <tbody>
                  {c.areas.flatMap((a) => a.items.map((i, k) => (
                    <tr key={i.id}>
                      {k === 0 && <td rowSpan={a.items.length}><b>{a.area}</b></td>}
                      <td><Link href={`/catalog/${i.id}`}>{i.id}</Link> {i.title}</td>
                      <td className="small">{i.reference}</td>
                      <td className={`num ${i.flagged ? "bad" : ""}`}>{i.flagged}</td>
                      <td className="num">{i.checked} {i.unit}</td>
                      <td className="num">{i.has_amount && i.flagged > 0 ? money(i.amount_affected) : ""}</td>
                      <td><StatusBadge s={i.error ? "error" : i.status} /></td>
                    </tr>
                  )))}
                </tbody>
              </table>
              <Formula>{c.formula} {c.disclaimer}</Formula>
            </div>
          );
        }}
      </Loading>
    </Gate>
  );
}
