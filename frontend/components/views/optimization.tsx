"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { BarChart, Waterfall } from "@/components/charts";
import { Gate, useMe } from "@/components/shell";
import { Formula, Head, Kpi, Loading, StatusBadge } from "@/components/ui";
import { post, useApi } from "@/lib/api";
import { compact, money } from "@/lib/format";

type Action = { OptimizationId: string; Origin: string; Lever: string; LeverLabel: string; Domain: string; Title: string; ScopeType: string;
  ScopeKey: string; CostCenter: string; Owner: string; Confidence: string; Status: string; EstimatedMonthlySavings: number;
  BaselineMonthlyCost: number; VerifiedMonthlySavings: number | null; VerificationBasis: string | null; ImplementedBy: string | null;
  IdentifiedOn: string; ImplementedOn: string | null; VerifiedOn: string | null };
type Opt = { stages: { Stage: string; MonthlySavings: number; Actions: number }[];
  by_lever: { Lever: string; LeverLabel: string; Domain: string; Identified: number; Verified: number; Actions: number; ProviderRecommended: number }[];
  realized_by_month: { Month: string; VerifiedMonthlySavings: number }[]; verified_monthly: number; verified_annual: number; formula: string; actions: Action[] };

const NEXT: Record<string, [string, string, string][]> = {  // status -> [action, label, permission]
  identified: [["approve", "Approve", "recommendation.approve"], ["reject", "Reject", "recommendation.approve"]],
  approved: [["implement", "Mark implemented", "optimization.implement"], ["reject", "Reject", "recommendation.approve"]],
  implemented: [["verify", "Verify savings", "optimization.verify"]],
};

export function OptimizationView() {
  const { me, version, refresh } = useMe();
  const focus = useSearchParams().get("focus");
  const [domain, setDomain] = useState<"" | "cloud" | "ai">("");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const state = useApi<Opt>("/optimization", version);
  async function decide(a: Action, action: string) {
    setBusy(a.OptimizationId + action); setMsg(null);
    try {
      const r = await post<{ action: Action; ledger: { Seq: number } }>(`/optimization/${a.OptimizationId}/decision`, { action });
      const v = r.action.VerifiedMonthlySavings;
      setMsg(`${a.OptimizationId}: ${action} recorded (ledger entry ${r.ledger.Seq})${action === "verify" && v != null ? ` · verified ${money(v)}/month (${r.action.VerificationBasis})` : ""}. Tax evidence re-tested.`);
      refresh();
    } catch (e) { setMsg((e as Error).message); } finally { setBusy(null); }
  }
  return (
    <Gate module="optimization">
      <Head title="Optimization Center">
        {(["", "cloud", "ai"] as const).map((d) => <button key={d} className={`chip ${domain === d ? "on" : ""}`} onClick={() => setDomain(d)}>{d === "" ? "All" : d === "ai" ? "AI & LLM" : "Cloud"}</button>)}
      </Head>
      <Loading state={state}>
        {(o) => {
          const acts = o.actions.filter((a) => !domain || a.Domain === domain).sort((a, b) =>
            (a.OptimizationId === focus ? -1 : b.OptimizationId === focus ? 1 : 0) || b.EstimatedMonthlySavings - a.EstimatedMonthlySavings);
          const levers = o.by_lever.filter((l) => !domain || l.Domain === domain);
          return (
            <div className="stack">
              <div className="grid g4">
                {o.stages.map((s) => <Kpi key={s.Stage} label={s.Stage} value={`${money(s.MonthlySavings)}/mo`} tone={s.Stage === "Verified" ? "good" : ""}
                  sub={`${s.Actions} ${s.Actions === 1 ? "action" : "actions"}${s.Stage === "Verified" ? ` · ${compact(o.verified_annual)}/yr` : ""}`} />)}
              </div>
              <div className="grid g2">
                <div className="card"><h2>Savings lifecycle (monthly)</h2>
                  <Waterfall steps={o.stages.map((s) => ({ label: s.Stage, value: s.MonthlySavings, tone: s.Stage === "Verified" ? "good" : undefined }))} /></div>
                <div className="card"><h2>Savings by lever</h2>
                  <BarChart horizontal stacked labels={levers.map((l) => `${l.LeverLabel}${l.Domain === "ai" ? " (AI)" : ""}`)}
                    datasets={[{ label: "Verified", data: levers.map((l) => l.Verified), backgroundColor: "#2e7d4f" },
                      { label: "Identified, not yet verified", data: levers.map((l) => Math.max(0, l.Identified - l.Verified)), backgroundColor: "#0f6ec8" }]} /></div>
              </div>
              {msg && <div className="notice">{msg}</div>}
              <div className="card flush">
                <h2>Backlog</h2>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Action</th><th>Lever</th><th>Scope</th><th className="num">Baseline /mo</th><th className="num">Estimated /mo</th><th className="num">Verified /mo</th><th>Status</th><th>Next step</th></tr></thead>
                    <tbody>
                      {acts.map((a) => (
                        <tr key={a.OptimizationId} className={a.OptimizationId === focus ? "focus" : ""}>
                          <td><span className="uc-id">{a.OptimizationId}</span> <b>{a.Title}</b>
                            <div className="small muted">{a.Origin.startsWith("UO") || a.Origin.startsWith("CO") || a.Origin.startsWith("PH")
                              ? <Link href={`/catalog/${a.Origin}`}>{a.Origin}</Link> : a.Origin} · {a.Confidence === "provider_recommended" ? "provider recommended" : "CostIQ estimate"}</div></td>
                          <td>{a.LeverLabel}<div className="small muted">{a.Domain === "ai" ? "AI & LLM" : "Cloud"}</div></td>
                          <td className="small">{a.ScopeKey}<div className="muted">{a.CostCenter}</div></td>
                          <td className="num">{money(a.BaselineMonthlyCost)}</td>
                          <td className="num">{money(a.EstimatedMonthlySavings)}</td>
                          <td className="num">{a.VerifiedMonthlySavings != null ? <><b>{money(a.VerifiedMonthlySavings)}</b><div className="small muted">{a.VerificationBasis}</div></> : "—"}</td>
                          <td><StatusBadge s={a.Status} />{a.ImplementedBy && <div className="small muted">by {a.ImplementedBy.split("@")[0].replace("demo.", "")}</div>}</td>
                          <td><div className="row">{(NEXT[a.Status] ?? []).filter(([, , perm]) => me.actions.includes(perm)).map(([act, lab]) => (
                            <button key={act} className={`btn ${act === "reject" ? "" : "primary"}`} disabled={!!busy} onClick={() => decide(a, act)}>{lab}</button>))}</div></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              {o.realized_by_month.length > 0 && <div className="card"><h2>Realized savings by verification month</h2>
                <BarChart height="short" labels={o.realized_by_month.map((r) => r.Month)}
                  datasets={[{ label: "Verified monthly savings", data: o.realized_by_month.map((r) => r.VerifiedMonthlySavings), backgroundColor: "#2e7d4f" }]} /></div>}
              <Formula>{o.formula}</Formula>
            </div>
          );
        }}
      </Loading>
    </Gate>
  );
}
