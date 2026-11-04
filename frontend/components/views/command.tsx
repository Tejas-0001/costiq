"use client";
// Command Center: the three numbers, the month's position and the findings.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { BarChart } from "@/components/charts";
import { useCopy } from "@/components/copy";
import { homeFor, PERIODS, useMe } from "@/components/shell";
import { Findings, type Finding } from "@/components/usecases";
import { Loading, RecordTable } from "@/components/ui";
import { useApi } from "@/lib/api";
import { compact, money } from "@/lib/format";

type Headline = {
  deduction_at_risk: number; evidence_score: number; credit_unlock: number; credit_claimed: number;
  verified_savings_annual: number; verified_savings_monthly: number; filings: string[];
  top_risk: { Label: string; Item: string; Vendor: string; FilingId: string; Risk: number }[];
  by_reason: { RiskReason: string; Label: string; Risk: number }[];
  jurisdictions?: { Jurisdiction: string; Credit: number; CreditStatus: string }[];
  stages?: { Stage: string; MonthlySavings: number }[];
};
type Unit = { CostCenter: string; SpendInPeriod: number; MonthToDate: number; Forecast: number; Budget: number | null; Variance: number | null; VariancePct: number | null; Status: string };
type Command = {
  open_month: string; days_elapsed: number; days_in_month: number; month_to_date: number; forecast: number; budget: number;
  variance: number; verified_savings: number; negotiated_savings: number; exposure_incurred: number; opportunity_estimated: number;
  findings: Finding[]; trend: { Month: string; Cloud: number; AI: number }[]; units: Unit[];
  confidence?: { score: number; formula: string; parts: Record<string, number> };
  formulas: Record<string, string>; as_of: string; headline?: Headline; period: { long: string; label: string };
};
type PeriodData = {
  range: string; start: string; end: string; total: number; cloud: number; ai: number;
  previous: number | null; previous_start: string | null; previous_end: string | null; change_pct: number | null;
  providers: { Provider: string; Cost: number }[];
};

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const PART: Record<string, string> = {
  month_elapsed: "Month elapsed", implemented_adapter_coverage: "Live adapters", allocation_coverage: "Allocation", prior_period_tie_out: "Tie-out",
};

function PeriodPanel() {
  const { period, version } = useMe();
  const st = useApi<PeriodData>(`/period?range=${period}`, version);
  const p = PERIODS.find((x) => x.key === period)!;
  return (
    <Loading state={st}>
      {(d) => (
        <div className="grid g2">
          <div className="card kpi">
            <div className="label">Spend, {p.long}</div>
            <div className="value">{money(d.total)}</div>
            <div className="sub">{d.start === d.end ? d.start : `${d.start} to ${d.end}`} · {d.change_pct == null ? "no prior period" : `${d.change_pct > 0 ? "+" : ""}${d.change_pct.toFixed(1)}% vs prior`}</div>
          </div>
          <div className="card kpi">
            <div className="label">Cloud &amp; SaaS / AI</div>
            <div className="value">{money(d.cloud)} <span className="muted" style={{ fontWeight: 400 }}>/</span> {money(d.ai)}</div>
            <div className="sub">{d.providers.slice(0, 4).map((x) => `${x.Provider} ${compact(x.Cost)}`).join(" · ")}</div>
          </div>
        </div>
      )}
    </Loading>
  );
}

function ThreeNumbers({ h }: { h: Headline }) {
  const { t } = useCopy();
  // the tenant names the risk and unlock figures (command.risk, command.unlock) and the jurisdiction statuses
  const status = (s: string) => t(`command.status.${s}`, undefined, s);
  return (
    <div className="grid g3">
      <div className="card kpi bad">
        <div className="label">{t("command.risk.label")}</div>
        <div className="value">{money(h.deduction_at_risk)}</div>
        <div className="sub">{t("command.risk.sub", { score: h.evidence_score, filings: h.filings.length })}</div>
        <div className="foot">{h.by_reason.map((r) => `${r.Label}: ${compact(r.Risk)}`).join(" · ")} · <Link href="/risk">{t("nav.items.risk")}</Link></div>
      </div>
      <div className="card kpi good">
        <div className="label">{t("command.unlock.label")}</div>
        <div className="value">{compact(h.credit_unlock)}/yr</div>
        <div className="sub">{t("command.unlock.sub", { amount: compact(h.credit_claimed) })}</div>
        <div className="foot">{(h.jurisdictions ?? []).map((j) => `${j.Jurisdiction} ${compact(j.Credit)} (${status(j.CreditStatus)})`).join(" · ")} · <Link href="/tax?tab=credit">By jurisdiction</Link></div>
      </div>
      <div className="card kpi good">
        <div className="label">Verified savings</div>
        <div className="value">{compact(h.verified_savings_annual)}/yr</div>
        <div className="sub">{compact(h.verified_savings_monthly)}/mo</div>
        <div className="foot">{(h.stages ?? []).map((s) => `${s.Stage}: ${compact(s.MonthlySavings)}/mo`).join(" · ")} · <Link href="/optimization">Optimization Center</Link></div>
      </div>
    </div>
  );
}

function MonthPosition({ k }: { k: Command }) {
  const m = MON[+k.open_month.slice(5, 7) - 1];
  return (
    <div className="grid g4">
      <div className={`card kpi ${k.variance > 0 ? "bad" : "good"}`}>
        <div className="label">Forecast vs budget</div>
        <div className="value">{money(k.variance)}</div>
        <div className="sub">{money(k.forecast)} forecast · {money(k.budget)} budget</div>
      </div>
      <div className="card kpi">
        <div className="label">{m} to date</div>
        <div className="value">{money(k.month_to_date)}</div>
        <div className="sub">Day {k.days_elapsed} of {k.days_in_month}</div>
      </div>
      <div className="card kpi good">
        <div className="label">Savings verified</div>
        <div className="value">{money(k.verified_savings)}</div>
        <div className="sub">Negotiated {money(k.negotiated_savings)} · identified {money(k.opportunity_estimated)}</div>
      </div>
      <div className="card kpi" title={k.confidence?.formula}>
        <div className="label">Forecast confidence</div>
        <div className="value">{k.confidence ? `${k.confidence.score}%` : "—"}</div>
        <div className="sub">{k.confidence ? Object.entries(k.confidence.parts).map(([a, v]) => `${PART[a] ?? a} ${Math.round(v)}%`).join(" · ") : ""}</div>
      </div>
    </div>
  );
}

export function CommandView() {
  const { me, version, refresh, period } = useMe();
  const router = useRouter();
  const allowed = me.modules.includes("command");
  useEffect(() => {
    if (!allowed) router.replace(homeFor(me.modules));
  }, [allowed, me.modules, router]);
  const state = useApi<Command>(allowed ? `/command?range=${period}` : null, version);
  if (!allowed) return null;
  return (
    <div className="stack">
      <h1>Command Center</h1>
      <PeriodPanel />
      <Loading state={state}>
        {(k) => (
          <>
            {k.headline && <ThreeNumbers h={k.headline} />}
            <MonthPosition k={k} />
            <div className="grid g2w">
              <div className="card">
                <div className="card-head"><h2>Findings</h2><Link className="small" href="/anomalies">All</Link></div>
                <Findings items={k.findings} onDecided={refresh} />
              </div>
              <div className="card">
                <h2>Spend by month</h2>
                <BarChart labels={k.trend.map((t) => MON[+t.Month.slice(5, 7) - 1])} stacked height="short"
                  datasets={[{ label: "Cloud and SaaS", data: k.trend.map((t) => t.Cloud) }, { label: "AI", data: k.trend.map((t) => t.AI) }]} />
              </div>
            </div>
            <div className="card flush">
              <h2>Cost centers, {MON[+k.open_month.slice(5, 7) - 1]} <span className="small muted">· spend in period: {k.period.label}</span></h2>
              <RecordTable rows={k.units} columns={["CostCenter", "SpendInPeriod", "MonthToDate", "Forecast", "Budget", "Variance", "VariancePct", "Status"]} />
            </div>
          </>
        )}
      </Loading>
    </div>
  );
}
