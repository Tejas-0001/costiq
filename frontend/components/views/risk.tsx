"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { useCopy } from "@/components/copy";
import { Gate, useMe } from "@/components/shell";
import { LineageDrawer } from "@/components/usecases";
import { Formula, Head, Kpi, Loading, RecordTable, StatusBadge } from "@/components/ui";
import { post, useApi } from "@/lib/api";
import { compact, money } from "@/lib/format";

type Fix = { kind: string; target: string | null; status?: string; label: string };
type Top = { RiskReason: string; Label: string; Vendor: string; Item: string; Application: string; CostCenter: string;
  LegalEntity: string; FilingId: string; Lines: number; FromDay: string; ToDay: string; Risk: number; Fix: Fix };
type Summary = {
  deduction_at_risk: number; review_period_spend: number; evidence_score: number; lines: number; lines_at_risk: number;
  by_reason: { RiskReason: string; Label: string; Lines: number; Risk: number }[];
  by_filing: { FilingId: string; LegalEntity: string; Jurisdiction: string; Status: string; Risk: number; Spend: number; Score: number }[];
  heatmap: { LegalEntity: string; Vendor: string; Spend: number; Risk: number; Score: number }[];
  top: Top[]; filings: { FilingId: string; LegalEntity: string; Status: string }[]; formula: string; disclaimer: string;
};

const LEVER_FOR: Record<string, string> = { no_benefit: "idle", unactioned_savings: "rightsizing" };

/** Entity x vendor evidence score as a plain table; a cell drills into its lines. */
function Heatmap({ cells, onPick }: { cells: Summary["heatmap"]; onPick: (entity: string, vendor: string) => void }) {
  const vendors = useMemo(() => {
    const spend = new Map<string, number>();
    cells.forEach((c) => spend.set(c.Vendor, (spend.get(c.Vendor) || 0) + c.Spend));
    return [...spend.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8).map((e) => e[0]);
  }, [cells]);
  const entities = [...new Set(cells.map((c) => c.LegalEntity))].sort();
  const at = (e: string, v: string) => cells.find((c) => c.LegalEntity === e && c.Vendor === v);
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>Entity</th>{vendors.map((v) => <th key={v} className="num">{v}</th>)}</tr></thead>
        <tbody>
          {entities.map((e) => (
            <tr key={e}>
              <td>{e}</td>
              {vendors.map((v) => {
                const c = at(e, v);
                if (!c) return <td key={v} className="num muted">—</td>;
                return (
                  <td key={v} className={`num ${c.Risk > 0 ? "bad" : ""}`}>
                    <a href="#lines" onClick={() => onPick(e, v)}>{c.Score}%</a>{c.Risk > 0 && <div className="small">{compact(c.Risk)}</div>}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FixButton({ t, onDone }: { t: Top; onDone: (msg: string) => void }) {
  const { me, refresh } = useMe();
  const copy = useCopy();
  const [busy, setBusy] = useState(false);
  const f = t.Fix;
  if (f.kind === "optimization") return <Link href={`/optimization?focus=${f.target}`}>{f.label}</Link>;
  if (f.kind === "reconciliation") return <Link href="/reconciliation">{f.label}</Link>;
  if (f.kind === "policy") return <Link href="/tax?tab=chain">{f.label}</Link>;
  if (f.kind !== "optimization_new") return <Link href={`/catalog/${copy.t("risk.useCases.glMap")}`}>{f.label}</Link>;  // GL account mapping
  if (!me.actions.includes("recommendation.submit")) return <span className="small muted">FinOps analyst to track</span>;
  return (
    <button className="btn" disabled={busy} onClick={async () => {
      setBusy(true);
      try {
        const a = await post<{ OptimizationId: string }>("/optimization", {
          origin: copy.t("risk.useCases.risk"), lever: LEVER_FOR[t.RiskReason] ?? "idle", title: copy.t("risk.fixTitle", { item: t.Item }),
          scope_type: "resource", scope_key: t.Item, cost_center: t.CostCenter, estimated_monthly_savings: 0 });
        onDone(`${a.OptimizationId} added to the Optimization Center.`);
        refresh();
      } catch (e) { onDone((e as Error).message); } finally { setBusy(false); }
    }}>Add to tracker</button>
  );
}

export function RiskView() {
  const { version } = useMe();
  const [filing, setFiling] = useState("");
  const [pick, setPick] = useState<{ reason?: string; entity?: string; vendor?: string }>({});
  const [lineage, setLineage] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const q = filing ? `?filing=${encodeURIComponent(filing)}` : "";
  const state = useApi<Summary>(`/risk${q}`, version);
  const lq = new URLSearchParams({ ...(filing ? { filing } : {}), ...(pick.reason ? { reason: pick.reason } : {}),
    ...(pick.entity ? { entity: pick.entity } : {}), ...(pick.vendor ? { vendor: pick.vendor } : {}) }).toString();
  const lines = useApi<Record<string, unknown>[]>(`/risk/lines?${lq}`, version);
  const { t } = useCopy();
  return (
    <Gate module="risk">
      <Head title={t("nav.items.risk")}>
        <select value={filing} onChange={(e) => { setFiling(e.target.value); setPick({}); }}>
          <option value="">{t("risk.all")}</option>
          {state.data?.filings.map((d) => <option key={d.FilingId} value={d.FilingId}>{d.FilingId} · {d.LegalEntity}</option>)}
        </select>
      </Head>
      <Loading state={state}>
        {(s) => {
          const fixable = s.top.filter((t) => t.Fix.kind === "optimization" || t.Fix.kind === "optimization_new").reduce((a, t) => a + t.Risk, 0);
          return (
            <div className="stack">
              {msg && <div className="notice">{msg}</div>}
              <div className="grid g4">
                <Kpi label={t("risk.amount")} value={money(s.deduction_at_risk)} tone="bad"
                  sub={`${s.lines_at_risk.toLocaleString("en-US")} of ${s.lines.toLocaleString("en-US")} lines`} />
                <Kpi label="Evidence score" value={`${s.evidence_score}%`} />
                <Kpi label={t("risk.spend")} value={money(s.review_period_spend)} />
                <Kpi label="Risk after open fixes" value={money(Math.max(0, s.deduction_at_risk - fixable))} tone="good" />
              </div>
              <div className="grid g2">
                <div className="card flush">
                  <h2>By reason</h2>
                  <table>
                    <thead><tr><th>Reason</th><th className="num">Lines</th><th className="num">Risk</th></tr></thead>
                    <tbody>{s.by_reason.map((r) => (
                      <tr key={r.RiskReason}><td><a href="#lines" onClick={() => setPick({ reason: r.RiskReason })}>{r.Label}</a></td>
                        <td className="num">{r.Lines}</td><td className="num">{money(r.Risk)}</td></tr>
                    ))}</tbody>
                  </table>
                </div>
                <div className="card flush">
                  <h2>{t("risk.byFiling")}</h2>
                  <RecordTable rows={s.by_filing} columns={["FilingId", "LegalEntity", "Status", "Spend", "Risk", "Score"]}
                    onRow={(r) => { setFiling(String(r.FilingId)); setPick({}); }} />
                </div>
              </div>
              <div className="card flush">
                <h2>Evidence score by entity and vendor</h2>
                <Heatmap cells={s.heatmap} onPick={(entity, vendor) => setPick({ entity, vendor })} />
              </div>
              <div className="card flush">
                <h2>Largest risks</h2>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Reason</th><th>Item</th><th>Vendor</th><th>Entity</th><th>{t("risk.filing")}</th><th>Days</th><th className="num">Risk</th><th>Fix</th></tr></thead>
                    <tbody>
                      {s.top.map((t, i) => (
                        <tr key={i}>
                          <td><StatusBadge s={t.Label} /></td>
                          <td><b>{t.Item}</b><div className="small muted">{t.Application} · {t.CostCenter}</div></td>
                          <td>{t.Vendor}</td>
                          <td>{t.LegalEntity}</td>
                          <td className="id">{t.FilingId}</td>
                          <td className="small id">{t.FromDay === t.ToDay ? t.FromDay : `${t.FromDay} to ${t.ToDay}`}</td>
                          <td className="num"><b>{money(t.Risk, 0)}</b></td>
                          <td><FixButton t={t} onDone={setMsg} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              <div className="card flush" id="lines">
                <div className="card-head">
                  <h2>Lines at risk{Object.values(pick).some(Boolean) && <span className="small muted"> ({Object.values(pick).filter(Boolean).join(", ")})</span>}</h2>
                  {Object.values(pick).some(Boolean) && <button className="btn" onClick={() => setPick({})}>Clear filter</button>}
                </div>
                <Loading state={lines}>
                  {(ls) => <RecordTable rows={ls} max={200} onRow={(r) => setLineage(String(r.ChargeKey))}
                    columns={["ChargeDay", "FilingId", "LegalEntity", "Vendor", "ServiceName", "ResourceName", "GlAccount", "AllocatedCost", "RiskReason", "RiskAmount"]} />}
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
