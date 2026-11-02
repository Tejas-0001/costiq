"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { BarChart } from "@/components/charts";
import { Gate, useMe } from "@/components/shell";
import { LineageDrawer } from "@/components/usecases";
import { Formula, Head, Kpi, Loading, RecordTable, StatusBadge } from "@/components/ui";
import { post, useApi } from "@/lib/api";
import { compact, money } from "@/lib/format";

type Fix = { kind: string; target: string | null; status?: string; label: string };
type Top = { RiskReason: string; Label: string; Vendor: string; Item: string; Application: string; CostCenter: string;
  LegalEntity: string; Docket: string; Lines: number; FromDay: string; ToDay: string; Risk: number; Fix: Fix };
type Summary = {
  disallowance_risk: number; base_period_spend: number; prudence_score: number; lines: number; lines_at_risk: number;
  by_reason: { RiskReason: string; Label: string; Lines: number; Risk: number }[];
  by_docket: { Docket: string; LegalEntity: string; Jurisdiction: string; Status: string; Risk: number; Spend: number; Score: number }[];
  heatmap: { LegalEntity: string; Vendor: string; Spend: number; Risk: number; Score: number }[];
  top: Top[]; dockets: { Docket: string; LegalEntity: string; Status: string }[]; formula: string; disclaimer: string;
};

const LEVER_FOR: Record<string, string> = { not_used_and_useful: "idle", unactioned_savings: "rightsizing" };

function scoreColor(score: number): string {
  // 100 = fully evidenced (green), 95 = amber, 85 or below = red; translucent tints so they work in light and dark
  const tint = score >= 99.95 ? [18, 146, 79, 0.14] : score >= 95 ? [255, 176, 32, 0.2] : score >= 90 ? [255, 122, 69, 0.24] : [224, 57, 62, 0.28];
  return `rgba(${tint.join(",")})`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const shortDay = (d: string) => `${MONTHS[Number(d.slice(5, 7)) - 1]} ${Number(d.slice(8, 10))}`;

function Heatmap({ cells, onPick }: { cells: Summary["heatmap"]; onPick: (entity: string, vendor: string) => void }) {
  const vendors = useMemo(() => {
    const risk = new Map<string, number>();
    cells.forEach((c) => risk.set(c.Vendor, (risk.get(c.Vendor) || 0) + c.Spend));
    return [...risk.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8).map((e) => e[0]);
  }, [cells]);
  const entities = [...new Set(cells.map((c) => c.LegalEntity))].sort();
  const at = (e: string, v: string) => cells.find((c) => c.LegalEntity === e && c.Vendor === v);
  return (
    <div className="heat" style={{ gridTemplateColumns: `200px repeat(${vendors.length}, minmax(96px, 1fr))`, minWidth: 200 + vendors.length * 99 }}>
      <div className="h" />
      {vendors.map((v) => <div key={v} className="h" style={{ textAlign: "center", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={v}>{v}</div>)}
      {entities.map((e) => (
        <div key={e} style={{ display: "contents" }}>
          <div className="h" style={{ textTransform: "none", fontSize: 12.5, color: "var(--ink)" }}>{e}</div>
          {vendors.map((v) => {
            const c = at(e, v);
            if (!c) return <div key={v} className="cell empty">—</div>;
            return (
              <div key={v} className="cell" style={{ background: scoreColor(c.Score) }} onClick={() => onPick(e, v)}
                title={`${e} · ${v}: spend ${money(c.Spend)}, risk ${money(c.Risk)}, prudence score ${c.Score}%`}>
                {c.Score}%<small>{c.Risk > 0 ? `${compact(c.Risk)} at risk` : "evidenced"}</small>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function FixButton({ t, onDone }: { t: Top; onDone: (msg: string) => void }) {
  const { me, refresh } = useMe();
  const [busy, setBusy] = useState(false);
  const f = t.Fix;
  if (f.kind === "optimization") return <Link className="btn sm primary" href={`/optimization?focus=${f.target}`}>Fix it: {f.label}</Link>;
  if (f.kind === "reconciliation") return <Link className="btn sm" href="/reconciliation">Fix it: {f.label}</Link>;
  if (f.kind === "policy") return <Link className="btn sm" href="/regulatory?tab=chain">Fix it: {f.label}</Link>;
  if (f.kind === "ferc_map") return <Link className="btn sm" href="/catalog/UT-01">Fix it: {f.label}</Link>;
  if (!me.actions.includes("recommendation.submit")) return <span className="small muted">Ask the FinOps analyst to track it</span>;
  return (
    <button className="btn sm primary" disabled={busy} onClick={async () => {
      setBusy(true);
      try {
        const a = await post<{ OptimizationId: string }>("/optimization", {
          origin: "UT-12", lever: LEVER_FOR[t.RiskReason] ?? "idle", title: `Remove disallowance risk on ${t.Item}`,
          scope_type: "resource", scope_key: t.Item, cost_center: t.CostCenter, estimated_monthly_savings: 0 });
        onDone(`${a.OptimizationId} added to the Optimization Center.`);
        refresh();
      } catch (e) { onDone((e as Error).message); } finally { setBusy(false); }
    }}>Fix it: add to tracker</button>
  );
}

export function PrudenceView() {
  const { version } = useMe();
  const [docket, setDocket] = useState("");
  const [pick, setPick] = useState<{ reason?: string; entity?: string; vendor?: string }>({});
  const [lineage, setLineage] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const q = docket ? `?docket=${encodeURIComponent(docket)}` : "";
  const state = useApi<Summary>(`/prudence${q}`, version);
  const lq = new URLSearchParams({ ...(docket ? { docket } : {}), ...(pick.reason ? { reason: pick.reason } : {}),
    ...(pick.entity ? { entity: pick.entity } : {}), ...(pick.vendor ? { vendor: pick.vendor } : {}) }).toString();
  const lines = useApi<Record<string, unknown>[]>(`/prudence/lines?${lq}`, version);
  return (
    <Gate module="prudence">
      <Head title="Prudence Radar" sub="Base-period technology spend tested for the evidence a commission would ask for, docket by docket">
        <select value={docket} onChange={(e) => { setDocket(e.target.value); setPick({}); }}>
          <option value="">All open dockets</option>
          {state.data?.dockets.map((d) => <option key={d.Docket} value={d.Docket}>{d.Docket} · {d.LegalEntity}</option>)}
        </select>
      </Head>
      <Loading state={state}>
        {(s) => {
          const fixable = s.top.filter((t) => t.Fix.kind === "optimization" || t.Fix.kind === "optimization_new").reduce((a, t) => a + t.Risk, 0);
          return (
            <div className="stack">
              {msg && <div className="notice">{msg}</div>}
              <div className="grid g4">
                <Kpi label="Disallowance risk" value={money(s.disallowance_risk)} tone="bad"
                  sub={`${s.lines_at_risk.toLocaleString("en-US")} of ${s.lines.toLocaleString("en-US")} allocated lines`} />
                <Kpi label="Prudence score" value={`${s.prudence_score}%`} sub="cost-weighted share of evidence present" />
                <Kpi label="Base-period spend tested" value={money(s.base_period_spend)} sub="O&M and deferred subscriptions" />
                <Kpi label="Risk after open fixes" value={money(Math.max(0, s.disallowance_risk - fixable))} tone="good"
                  sub={`if the ${s.top.filter((t) => t.Fix.kind.startsWith("optimization")).length} optimizations shown are implemented`} />
              </div>
              <div className="grid g2">
                <div className="card">
                  <h2>Why spend is at risk</h2>
                  <BarChart horizontal labels={s.by_reason.map((r) => r.Label)} height="short"
                    datasets={[{ label: "Disallowance risk", data: s.by_reason.map((r) => r.Risk), backgroundColor: "#ff5f45" }]}
                    onClick={(l) => setPick({ reason: s.by_reason.find((r) => r.Label === l)?.RiskReason })} />
                </div>
                <div className="card">
                  <div className="card-head"><h2>Dockets</h2><span className="small muted">click a docket to focus on it</span></div>
                  <RecordTable rows={s.by_docket} columns={["Docket", "Status", "Spend", "Risk", "Score"]}
                    onRow={(r) => { setDocket(String(r.Docket)); setPick({}); }} />
                </div>
              </div>
              <div className="card">
                <div className="card-head"><h2>Evidence by entity and vendor</h2><span className="small muted">prudence score · click a cell to drill in</span></div>
                <div style={{ overflowX: "auto" }}>
                  <Heatmap cells={s.heatmap} onPick={(entity, vendor) => setPick({ entity, vendor })} />
                </div>
              </div>
              <div className="card flush">
                <div style={{ padding: "14px 16px 6px" }} className="card-head">
                  <h2 style={{ margin: 0 }}>Largest risks and how to remove them</h2>
                  <span className="small muted">each fix is recorded in the ledger and re-tested immediately</span>
                </div>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Reason</th><th>Item</th><th>Vendor</th><th>Entity · docket</th><th>Days</th><th className="num">Risk</th><th>Fix</th></tr></thead>
                    <tbody>
                      {s.top.map((t, i) => (
                        <tr key={i}>
                          <td><StatusBadge s={t.Label} /></td>
                          <td><b>{t.Item}</b><div className="small muted">{t.Application} · {t.CostCenter}</div></td>
                          <td>{t.Vendor}</td>
                          <td>{t.LegalEntity}<div className="small muted">{t.Docket}</div></td>
                          <td className="small id" title={`${t.FromDay} to ${t.ToDay}`}>{t.FromDay === t.ToDay ? shortDay(t.FromDay) : `${shortDay(t.FromDay)} – ${shortDay(t.ToDay)}`}</td>
                          <td className="num"><b>{money(t.Risk, 0)}</b></td>
                          <td><FixButton t={t} onDone={setMsg} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              <div className="card flush">
                <div style={{ padding: "14px 16px 6px" }} className="card-head">
                  <h2 style={{ margin: 0 }}>Lines at risk {Object.values(pick).some(Boolean) && <span className="small muted">· {Object.values(pick).filter(Boolean).join(" · ")}</span>}</h2>
                  <span className="row">{Object.values(pick).some(Boolean) && <button className="btn sm" onClick={() => setPick({})}>Clear filter</button>}
                    <span className="small muted">click a line for its lineage</span></span>
                </div>
                <Loading state={lines}>
                  {(ls) => <RecordTable rows={ls} max={200} onRow={(r) => setLineage(String(r.ChargeKey))}
                    columns={["ChargeDay", "Docket", "LegalEntity", "Vendor", "ServiceName", "ResourceName", "FercAccount", "AllocatedCost", "RiskReason", "RiskAmount"]} />}
                </Loading>
              </div>
              <Formula>{s.formula} {s.disclaimer}</Formula>
              {lineage && <LineageDrawer chargeKey={lineage} onClose={() => setLineage(null)} />}
            </div>
          );
        }}
      </Loading>
    </Gate>
  );
}
