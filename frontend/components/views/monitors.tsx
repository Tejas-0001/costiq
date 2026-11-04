"use client";
import { useState } from "react";
import { BarChart, LineChart } from "@/components/charts";
import { Gate, useMe, type PeriodInfo } from "@/components/shell";
import { Formula, Head, Kpi, Loading, RecordTable, StatusBadge } from "@/components/ui";
import { post, useApi, type Row } from "@/lib/api";
import { compact, money } from "@/lib/format";

// ------------------------------------------------------------------------------------------------ cloud
type Cloud = { as_of: string; open_month: string; period: PeriodInfo;
  kpis: { period_spend: number; untagged: number; untagged_pct: number | null; forecast: number; budget: number; variance: number;
    commitment_coverage_pct: number | null; unused_commitment_cost: number; open_anomalies: number; opportunity_monthly: number };
  daily: { Day: string; Cost: number; Anomaly: string | null }[]; movers: Row[]; by_provider: Row[]; by_service: Row[]; by_account: Row[];
  commitments: { coverage: Row[]; unused: Row[] };
  opportunities: { UseCase: string; Item: string; CostCenter: string; Finding: string; MonthlySavings: number; TrackerStatus: string | null }[];
  providers: string[]; formula: string };

const LEVER_OF: Record<string, string> = { "CO-01": "rightsizing", "CO-02": "idle", "CO-03": "scheduling", "CO-04": "rightsizing", "CO-05": "storage" };

export function CloudView() {
  const { me, version, refresh, period } = useMe();
  const [provider, setProvider] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const state = useApi<Cloud>(`/cloud-monitor?range=${period}${provider ? `&provider=${encodeURIComponent(provider)}` : ""}`, version);
  async function track(o: Cloud["opportunities"][number]) {
    try {
      const a = await post<{ OptimizationId: string }>("/optimization", { origin: o.UseCase, lever: LEVER_OF[o.UseCase] ?? "rightsizing",
        title: `${o.Finding}: ${o.Item}`, scope_type: "resource", scope_key: o.Item, cost_center: o.CostCenter, estimated_monthly_savings: o.MonthlySavings });
      setMsg(`${a.OptimizationId} is in the Optimization Center.`); refresh();
    } catch (e) { setMsg((e as Error).message); }
  }
  return (
    <Gate module="cloud">
      <Head title="Cloud Cost Monitor">
        <select value={provider} onChange={(e) => setProvider(e.target.value)}><option value="">All providers</option>
          {state.data?.providers.map((p) => <option key={p}>{p}</option>)}</select>
      </Head>
      <Loading state={state}>
        {(c) => (
          <div className="stack">
            <div className="grid g6">
              <Kpi label={`Cloud spend, ${c.period.long}`} value={compact(c.kpis.period_spend)} sub={c.period.label} />
              <Kpi label="Forecast vs budget" value={money(c.kpis.variance)} tone={c.kpis.variance > 0 ? "bad" : "good"} sub="all technology" />
              <Kpi label="Untagged" value={`${c.kpis.untagged_pct ?? 0}%`} sub={`${money(c.kpis.untagged)}, ${c.period.long}`} />
              <Kpi label="Commitment coverage" value={`${c.kpis.commitment_coverage_pct ?? "—"}%`} sub="eligible spend covered (F-41)" />
              <Kpi label="Open anomalies" value={c.kpis.open_anomalies} tone={c.kpis.open_anomalies ? "bad" : "good"} sub="last 60 days" />
              <Kpi label="Optimization potential" value={`${compact(c.kpis.opportunity_monthly)}/mo`} tone="good" sub="CO-01 to CO-05" />
            </div>
            <div className="card">
              <h2>Daily cloud spend (60 days)</h2>
              <LineChart labels={c.daily.map((d) => String(d.Day).slice(5, 10))}
                datasets={[{ label: "Daily effective cost", data: c.daily.map((d) => d.Cost),
                  pointRadius: c.daily.map((d) => (d.Anomaly ? 6 : 0)), pointBackgroundColor: c.daily.map((d) => (d.Anomaly ? "#ca4528" : "#0f6ec8")) }]} />
              {c.daily.filter((d) => d.Anomaly).map((d) => <div key={d.Day} className="small muted">⚠ {String(d.Day).slice(0, 10)}: {d.Anomaly}</div>)}
            </div>
            {msg && <div className="notice">{msg}</div>}
            <div className="card flush">
              <h2>What to optimize</h2>
              <div className="table-wrap"><table>
                <thead><tr><th>Check</th><th>Resource</th><th>Cost center</th><th>Finding</th><th className="num">Monthly savings</th><th>Tracker</th></tr></thead>
                <tbody>{c.opportunities.map((o, i) => (
                  <tr key={i}><td className="uc-id">{o.UseCase}</td><td><b>{o.Item}</b></td><td>{o.CostCenter ?? "—"}</td><td className="small">{o.Finding}</td>
                    <td className="num"><b>{money(o.MonthlySavings)}</b></td>
                    <td>{o.TrackerStatus && o.TrackerStatus !== "not tracked" ? <StatusBadge s={o.TrackerStatus} />
                      : me.actions.includes("recommendation.submit") ? <button className="btn" onClick={() => track(o)}>Add to tracker</button> : <span className="small muted">not tracked</span>}</td></tr>
                ))}</tbody></table></div>
            </div>
            <div className="grid g3">
              <div className="card"><h2>By provider <span className="small muted">· {c.period.long}</span></h2><BarChart horizontal height="short" labels={c.by_provider.map((r) => String(r.Provider))}
                datasets={[{ label: "Effective", data: c.by_provider.map((r) => Number(r.Effective)) }]} /></div>
              <div className="card"><h2>Top services <span className="small muted">· {c.period.long}</span></h2><BarChart horizontal height="short" labels={c.by_service.map((r) => String(r.Service))}
                datasets={[{ label: "Effective", data: c.by_service.map((r) => Number(r.Effective)), backgroundColor: "#454d53" }]} /></div>
              <div className="card flush"><h2>Week-over-week movers</h2>
                <RecordTable rows={c.movers} columns={["Service", "Provider", "Prior7Days", "Last7Days", "Change"]} /></div>
            </div>
            <div className="grid g2">
              <div className="card flush"><h2>Commitment coverage</h2>
                <RecordTable rows={c.commitments.coverage.map((r) => ({ ...r, CommitmentCoverageRate: `${(100 * Number(r.CommitmentCoverageRate)).toFixed(1)}%` }))} /></div>
              <div className="card flush"><h2>Unused commitments</h2>
                <RecordTable rows={c.commitments.unused} columns={["ServiceProviderName", "CommitmentDiscountType", "CommitmentDiscountStatus", "TotalEffectiveCost"]} /></div>
            </div>
            <Formula>{c.formula}</Formula>
          </div>
        )}
      </Loading>
    </Gate>
  );
}

// ------------------------------------------------------------------------------------------------ AI & LLM
type App = { Application: string; ModelName: string; Requests: number; AcceptedOutcomes: number; AcceptancePct: number; Cost: number;
  CostPerRequest: number; CostPerAcceptedOutcome: number; FailedPct?: number; RetriedPct?: number; InputGrowthPct?: number; SuggestedLevers: string[] };
type AI = { period: PeriodInfo; by_product: { Product: string; Provider: string; Effective: number }[]; open_month: string;
  kpis: { period_spend: number; cost_per_1m_tokens: number; cost_per_1m_month: string; cache_hit_pct: number; batch_share_pct: number;
    cost_per_accepted_outcome: number; seat_utilization_pct: number };
  by_month: { Month: string; Product: string; Effective: number }[];
  tokens: { Month: string; Input: number; Output: number; CacheRead: number; CacheWrite: number; CostPer1M: number }[];
  channel: Row[]; rag_components: { Application: string; Component: string; Effective: number }[]; apps: App[];
  provisioned: Row[]; idle_endpoints: Row[]; budgets: Row[]; routing: Row[]; seats: Row[]; new_models: Row[]; formula: string };

export function AIView() {
  const { version, period } = useMe();
  const state = useApi<AI>(`/ai-monitor?range=${period}`, version);
  return (
    <Gate module="ai">
      <Head title="AI & LLM Cost Monitor" />
      <Loading state={state}>
        {(a) => {
          const months = [...new Set(a.by_month.map((r) => r.Month))].sort();
          const products = [...new Set(a.by_month.map((r) => r.Product))];
          const rag = a.rag_components.filter((r) => r.Application === "doc-search-rag");
          return (
            <div className="stack">
              <div className="grid g6">
                <Kpi label={`AI spend, ${a.period.long}`} value={compact(a.kpis.period_spend)} sub={a.period.label} />
                <Kpi label="Cost per accepted outcome" value={money(a.kpis.cost_per_accepted_outcome, 3)} sub="all LLM apps (UO-13)" />
                <Kpi label="Blended cost / 1M tokens" value={money(a.kpis.cost_per_1m_tokens, 2)} sub={a.kpis.cost_per_1m_month} />
                <Kpi label="Prompt cache hit rate" value={`${a.kpis.cache_hit_pct}%`} sub="input served from cache (UO-01)" />
                <Kpi label="Batch share" value={`${a.kpis.batch_share_pct}%`} sub="of token spend at the 50% batch price" />
                <Kpi label="Seat utilization" value={`${a.kpis.seat_utilization_pct}%`} tone={a.kpis.seat_utilization_pct < 80 ? "bad" : "good"} sub="active / assigned (UO-06)" />
              </div>
              <div className="card flush">
                <h2>LLM applications: unit cost</h2>
                <div className="table-wrap"><table>
                  <thead><tr><th>Application</th><th>Model</th><th className="num">Requests</th><th className="num">Accepted</th><th className="num">Cost</th>
                    <th className="num">Per request</th><th className="num">Per accepted outcome</th><th>Signals</th><th>Suggested lever</th></tr></thead>
                  <tbody>{a.apps.map((x) => (
                    <tr key={x.Application}>
                      <td><b>{x.Application}</b></td><td className="small">{x.ModelName}</td>
                      <td className="num">{x.Requests.toLocaleString("en-US")}</td><td className="num">{x.AcceptancePct}%</td>
                      <td className="num">{money(x.Cost)}</td><td className="num">{money(x.CostPerRequest, 4)}</td>
                      <td className="num"><b>{money(x.CostPerAcceptedOutcome, 3)}</b></td>
                      <td className="small">{x.RetriedPct ? `retried ${x.RetriedPct}%` : ""}{x.InputGrowthPct && x.InputGrowthPct > 25 ? ` · context +${x.InputGrowthPct}%` : ""}</td>
                      <td>{x.SuggestedLevers.length ? x.SuggestedLevers.map((l) => <div key={l}><StatusBadge s={l} /></div>) : <span className="small muted">none</span>}</td>
                    </tr>))}</tbody></table></div>
              </div>
              <div className="grid g2">
                <div className="card"><h2>AI spend by product <span className="small muted">· {a.period.long}</span></h2>
                  <BarChart horizontal labels={a.by_product.map((r) => `${r.Product} (${r.Provider})`)}
                    datasets={[{ label: "Effective", data: a.by_product.map((r) => r.Effective) }]} /></div>
                <div className="card"><h2>AI spend by month</h2>
                  <BarChart stacked labels={months} datasets={products.map((p) => ({ label: p,
                    data: months.map((m) => a.by_month.filter((r) => r.Month === m && r.Product === p).reduce((s, r) => s + r.Effective, 0)) }))} /></div>
                <div className="card"><h2>Token mix by month</h2>
                  <BarChart stacked plain labels={a.tokens.map((t) => t.Month)}
                    datasets={[{ label: "Input", data: a.tokens.map((t) => t.Input) }, { label: "Output", data: a.tokens.map((t) => t.Output) },
                      { label: "Cache read", data: a.tokens.map((t) => t.CacheRead) }, { label: "Cache write", data: a.tokens.map((t) => t.CacheWrite) }]} /></div>
              </div>
              <div className="grid g3">
                <div className="card"><h2>Provisioned capacity (UO-10)</h2>
                  {a.provisioned.map((p) => (
                    <div key={String(p.Deployment)} className="stack" style={{ gap: 6 }}>
                      <b>{String(p.Deployment)} · {String(p.Units)} PTU · {String(p.ModelName)}</b>
                      <div className="small">{String(p.AvgUtilizationPct)}% average utilization · {money(p.ProvisionedCost)} provisioned vs {money(p.PayAsYouGoEquivalent)} pay-as-you-go</div>
                      <StatusBadge s={p.Finding} /><div className="small"><b>{money(p.MonthlySavingsIfPayg)}/month</b> if moved to pay-as-you-go</div>
                    </div>))}
                </div>
                <div className="card"><h2>Idle GPU and model endpoints (UO-09)</h2>
                  {a.idle_endpoints.map((e) => (
                    <div key={String(e.ResourceName)} style={{ marginBottom: 8 }}>
                      <b>{String(e.ResourceName)}</b><div className="small muted">{String(e.Application)} · {String(e.CostCenter)}</div>
                      <div className="small">{String(e.IdleDays)} of {String(e.DaysBilled)} days with zero requests · idle cost <b>{money(e.IdleCost)}</b></div>
                    </div>))}
                </div>
                <div className="card"><h2>RAG cost structure (doc-search-rag)</h2>
                  <BarChart horizontal height="short" labels={rag.map((r) => r.Component)}
                    datasets={[{ label: "Effective", data: rag.map((r) => r.Effective), backgroundColor: "#454d53" }]} />
                </div>
              </div>
              <div className="card flush"><h2>Model routing what-if (UO-14)</h2>
                <RecordTable rows={a.routing} columns={["Application", "CurrentModel", "CandidateModel", "AvgOutputTokens", "CurrentCost", "CandidateCost", "Savings", "SavingsPct", "Guidance"]} /></div>
              <div className="card flush"><h2>AI budget burn-down (BF-04)</h2>
                <RecordTable rows={a.budgets} columns={["CostCenter", "BudgetAmount", "MonthToDate", "BurnPct", "ProjectedExhaustion", "Status"]} /></div>
              <div className="grid g2">
                <div className="card flush"><h2>Seats (UO-06)</h2><RecordTable rows={a.seats} /></div>
                <div className="card flush"><h2>Same model, different channel <span className="small muted">· {a.period.long}</span></h2><RecordTable rows={a.channel} /></div>
              </div>
              <Formula>{a.formula}</Formula>
            </div>
          );
        }}
      </Loading>
    </Gate>
  );
}

// ------------------------------------------------------------------------------------------------ alerts
type Alert = { EventId: string; RuleId: string; RuleName: string; Kind: string; FiredAt: string; Severity: string; Subject: string; Message: string;
  Value: number; Acknowledged: boolean; AcknowledgedBy: string | null };

export function AlertsView() {
  const { version, refresh, period } = useMe();
  const [all, setAll] = useState(false);
  const state = useApi<{ events: Alert[]; unacknowledged: number; open_total: number; rules: Row[]; period?: PeriodInfo }>(
    all ? "/alerts" : `/alerts?range=${period}`, version);
  return (
    <div className="stack">
      <Head title="Alerts" />
      <Loading state={state}>
        {(a) => (
          <>
            <div className="row small">
              {a.period ? <span>Fired {a.period.long} ({a.period.label.replace(/^[^(]*\(|\)$/g, "")}): <b>{a.events.length}</b>. {a.open_total} open in all.</span>
                : <span>All fired alerts. {a.open_total} open.</span>}
              <button type="button" className="btn sm" onClick={() => setAll(!all)}>{all ? "Only the selected period" : "Show all"}</button>
            </div>
            {a.events.length === 0 && <div className="notice">No alerts fired {a.period?.long ?? ""}.</div>}
            <div className="card flush">
              <div className="table-wrap"><table>
                <thead><tr><th>Severity</th><th>Alert</th><th>Rule</th><th className="num">Value</th><th>Fired</th><th /></tr></thead>
                <tbody>{a.events.map((e) => (
                  <tr key={e.EventId} style={e.Acknowledged ? { opacity: 0.55 } : undefined}>
                    <td><StatusBadge s={e.Severity} /></td><td>{e.Message}</td><td className="small">{e.RuleName}</td>
                    <td className="num">{money(e.Value)}</td><td className="small">{String(e.FiredAt).slice(0, 10)}</td>
                    <td>{e.Acknowledged ? <span className="small muted">acknowledged by {e.AcknowledgedBy?.split("@")[0]}</span>
                      : <button className="btn" onClick={() => post(`/alerts/${e.EventId}/ack`).then(refresh)}>Acknowledge</button>}</td>
                  </tr>))}</tbody></table></div>
            </div>
            <div className="card flush"><h2>Rules</h2><RecordTable rows={a.rules} /></div>
          </>
        )}
      </Loading>
    </div>
  );
}
