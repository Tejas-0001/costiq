"use client";
// Command Center: numbers are shown as shapes (bars, bullets, rings), not sentences. Method notes sit behind ⓘ.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { motion } from "motion/react";
import { useEffect } from "react";
import { BarChart } from "@/components/charts";
import { CountUp, EASE } from "@/components/motion";
import { homeFor, PERIODS, useMe } from "@/components/shell";
import { Findings, type Finding } from "@/components/usecases";
import { InfoTip, Loading, Ring } from "@/components/ui";
import { useApi } from "@/lib/api";
import { compact, money } from "@/lib/format";

type Headline = {
  disallowance_risk: number; prudence_score: number; earnings_unlock: number; earned_today: number;
  verified_savings_annual: number; verified_savings_monthly: number; dockets: string[];
  top_risk: { Label: string; Item: string; Vendor: string; Docket: string; Risk: number }[];
  by_reason: { RiskReason: string; Label: string; Risk: number }[];
  jurisdictions?: { Jurisdiction: string; SteadyStateReturn: number; Earned: boolean; DeferralStatus: string }[];
  stages?: { Stage: string; MonthlySavings: number }[];
};
type Unit = { CostCenter: string; MonthToDate: number; Forecast: number; Budget: number | null; Variance: number | null; VariancePct: number | null; Status: string };
type Command = {
  open_month: string; days_elapsed: number; days_in_month: number; month_to_date: number; forecast: number; budget: number;
  variance: number; verified_savings: number; negotiated_savings: number; exposure_incurred: number; opportunity_estimated: number;
  findings: Finding[]; trend: { Month: string; Cloud: number; AI: number }[]; units: Unit[];
  confidence?: { score: number; formula: string; parts: Record<string, number> };
  formulas: Record<string, string>; as_of: string; headline?: Headline;
};
type PeriodData = {
  range: string; start: string; end: string; total: number; cloud: number; ai: number;
  previous: number | null; previous_start: string | null; previous_end: string | null; change_pct: number | null;
  daily: { Day: string; Cloud: number; AI: number; InPeriod: boolean }[]; providers: { Provider: string; Cost: number }[];
};

const usd = (n: number) => money(n);
const short = (n: number) => compact(n);
const perYear = (n: number) => `${compact(n)}/yr`;
const pctFmt = (n: number) => `${n.toFixed(1)}%`;
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const day = (s: string) => `${MON[+s.slice(5, 7) - 1]} ${+s.slice(8, 10)}`;
const span = (a: string, b: string) => (a === b ? day(a) : `${day(a)} – ${day(b)}`);
const grow = (delay = 0) => ({ duration: 0.9, ease: EASE, delay });

// ------------------------------------------------------------------------------------------------ spend for the period
function DailyBars({ d }: { d: PeriodData }) {
  const totals = d.daily.map((x) => x.Cloud + x.AI);
  // monthly subscriptions book on the 1st and dwarf usage days; scale to typical days and clip the spikes
  const sorted = [...totals].sort((a, b) => a - b);
  const typical = sorted[Math.floor(sorted.length * 0.9)] || 1;
  const cap = Math.max(...totals) > typical * 3 ? typical * 1.25 : Math.max(...totals);
  return (
    <div className="dbars" key={d.range}>
      {d.daily.map((x, i) => {
        const t = x.Cloud + x.AI, clip = t > cap, h = Math.min(t, cap) / cap;
        return (
          <div key={x.Day} className={`dbar ${x.InPeriod ? "" : "out"} ${clip ? "clip" : ""}`} title={`${day(x.Day)}: ${money(t)} (AI ${money(x.AI)})`}>
            {clip && <span className="spike">{short(t)}</span>}
            <motion.span className="stack" initial={{ scaleY: 0 }} animate={{ scaleY: 1 }} transition={grow(Math.min(i, 30) * 0.012)} style={{ height: `${h * 100}%` }}>
              <i className="ai" style={{ height: `${(x.AI / t) * 100}%` }} /><i className="cl" />
            </motion.span>
          </div>
        );
      })}
    </div>
  );
}

function PeriodPanel() {
  const { period, version } = useMe();
  const st = useApi<PeriodData>(`/period?range=${period}`, version);
  const d = st.data;
  const p = PERIODS.find((x) => x.key === period)!;
  if (st.error) return <div className="card"><div className="error">{st.error.message}</div></div>;
  const max = d ? Math.max(1, ...d.providers.map((x) => x.Cost)) : 1;
  const down = d?.change_pct != null && d.change_pct < 0;
  return (
    <div className={`card pspend ${st.loading ? "busy" : ""}`}>
      <div className="pspend-num">
        <span className="eyebrow">Spend · {p.long}</span>
        <CountUp className="big" value={d?.total} format={usd} duration={1.1} />
        <span className="dim">{d ? span(d.start, d.end) : " "}</span>
        {d && (d.change_pct != null
          ? <span className={`delta ${down ? "good" : "bad"}`}>{down ? "▼" : "▲"} {Math.abs(d.change_pct).toFixed(1)}%<em>vs {span(d.previous_start!, d.previous_end!)}</em></span>
          : <span className="delta flat">No prior period in the data</span>)}
        {d && (
          <div className="split">
            <div className="split-bar">
              <motion.i className="cl" initial={{ width: 0 }} animate={{ width: `${(d.cloud / Math.max(1, d.total)) * 100}%` }} transition={grow(0.2)} />
              <motion.i className="ai" initial={{ width: 0 }} animate={{ width: `${(d.ai / Math.max(1, d.total)) * 100}%` }} transition={grow(0.3)} />
            </div>
            <div className="split-key"><span><i className="cl" />Cloud &amp; SaaS <b>{short(d.cloud)}</b></span><span><i className="ai" />AI <b>{short(d.ai)}</b></span></div>
          </div>
        )}
      </div>
      <div className="pspend-chart">{d && <DailyBars d={d} />}</div>
      <div className="pspend-prov">
        <span className="eyebrow">Top providers</span>
        {d?.providers.map((x, i) => (
          <div key={x.Provider} className="hrow">
            <span className="lbl">{x.Provider}</span>
            <span className="track"><motion.i initial={{ width: 0 }} animate={{ width: `${(x.Cost / max) * 100}%` }} transition={grow(0.1 + i * 0.05)} /></span>
            <span className="amt">{short(x.Cost)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ the three numbers
const SHORT: Record<string, string> = {
  unactioned_savings: "Savings unactioned", not_used_and_useful: "Not used and useful", unreconciled: "Not reconciled",
  unmapped_ferc: "No FERC account", no_approved_policy: "No approved policy",
};

function ThreeNumbers({ h }: { h: Headline }) {
  const rmax = Math.max(1, ...h.by_reason.map((r) => r.Risk));
  const js = [...(h.jurisdictions ?? [])].sort((a, b) => b.SteadyStateReturn - a.SteadyStateReturn);
  const jmax = Math.max(1, ...js.map((j) => j.SteadyStateReturn));
  const st = h.stages ?? [];
  const smax = Math.max(1, ...st.map((s) => s.MonthlySavings));
  return (
    <div className="grid g3 three">
      <Link href="/prudence" className="card tn risk">
        <div className="tn-head"><span className="eyebrow">Disallowance risk</span>
          <Ring value={h.prudence_score} size={44} tone="good"><b>{Math.round(h.prudence_score)}</b></Ring></div>
        <CountUp className="tn-num" value={h.disallowance_risk} format={short} />
        <span className="tn-sub">{h.prudence_score}% of base-period spend evidenced · {h.dockets.length} open dockets</span>
        <div className="tn-viz">
          {h.by_reason.map((r, i) => (
            <div key={r.RiskReason} className="hrow">
              <span className="lbl">{SHORT[r.RiskReason] ?? r.Label}</span>
              <span className="track"><motion.i className="risk" initial={{ width: 0 }} animate={{ width: `${(r.Risk / rmax) * 100}%` }} transition={grow(0.2 + i * 0.06)} /></span>
              <span className="amt">{short(r.Risk)}</span>
            </div>
          ))}
        </div>
        <span className="go">Prudence Radar →</span>
      </Link>
      <Link href="/regulatory?tab=earnings" className="card tn unlock">
        <div className="tn-head"><span className="eyebrow">Earnings unlock</span><span className="chip-s">{perYear(h.earned_today)} earned today</span></div>
        <CountUp className="tn-num" value={h.earnings_unlock} format={perYear} />
        <span className="tn-sub">Return if cloud fees were a regulatory asset</span>
        <div className="tn-viz cols">
          {js.map((j, i) => (
            <div key={j.Jurisdiction} className="col" title={`${j.Jurisdiction}: ${money(j.SteadyStateReturn)}/yr${j.Earned ? " (earned under an order)" : " (if deferral were allowed)"}`}>
              <span className="bar"><motion.i className={j.Earned ? "earned" : "whatif"} initial={{ height: 0 }} animate={{ height: `${(j.SteadyStateReturn / jmax) * 100}%` }}
                transition={grow(0.2 + i * 0.07)} /></span>
              <span className="st">{j.Jurisdiction}</span>
            </div>
          ))}
        </div>
        <span className="go">Model each state →</span>
      </Link>
      <Link href="/optimization" className="card tn save">
        <div className="tn-head"><span className="eyebrow">Verified savings</span><span className="chip-s">{short(h.verified_savings_monthly)}/mo</span></div>
        <CountUp className="tn-num" value={h.verified_savings_annual} format={perYear} />
        <span className="tn-sub">Measured after each fix, carried into the rate case</span>
        <div className="tn-viz">
          {st.map((s, i) => (
            <div key={s.Stage} className={`hrow ${s.Stage === "Verified" ? "hi" : ""}`}>
              <span className="lbl">{s.Stage}</span>
              <span className="track"><motion.i className={s.Stage === "Verified" ? "save" : "muted"} initial={{ width: 0 }}
                animate={{ width: `${Math.max(2, (s.MonthlySavings / smax) * 100)}%` }} transition={grow(0.2 + i * 0.06)} /></span>
              <span className="amt">{short(s.MonthlySavings)}</span>
            </div>
          ))}
        </div>
        <span className="go">Optimization Center →</span>
      </Link>
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ month position
/** Bullet graph: spent so far (solid), forecast (light), budget (marker). */
function Bullet({ mtd, forecast, budget, delay = 0 }: { mtd: number; forecast: number; budget: number | null; delay?: number }) {
  const scale = Math.max(forecast, budget ?? 0, mtd, 1) * 1.08;
  const over = budget != null && forecast > budget;
  return (
    <span className={`bullet ${over ? "over" : ""}`}>
      <motion.i className="fc" initial={{ width: 0 }} animate={{ width: `${(forecast / scale) * 100}%` }} transition={grow(delay)} />
      <motion.i className="mtd" initial={{ width: 0 }} animate={{ width: `${(mtd / scale) * 100}%` }} transition={grow(delay + 0.1)} />
      {budget != null && <motion.b className="bud" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: delay + 0.5 }} style={{ left: `${(budget / scale) * 100}%` }} />}
    </span>
  );
}

const PART: Record<string, string> = {
  month_elapsed: "Month elapsed", implemented_adapter_coverage: "Live adapters", allocation_coverage: "Allocation", prior_period_tie_out: "Tie-out",
};

function MonthPosition({ k }: { k: Command }) {
  const under = k.variance <= 0;
  return (
    <div className="grid g3">
      <div className="card mp">
        <div className="mp-head"><span className="eyebrow">{MON[+k.open_month.slice(5, 7) - 1]} forecast vs budget</span><InfoTip text={k.formulas.forecast} /></div>
        <div className="mp-row">
          <CountUp className="mp-num" value={Math.abs(k.variance)} format={short} />
          <span className={`delta ${under ? "good" : "bad"}`}>{under ? "under budget" : "over budget"}</span>
        </div>
        <Bullet mtd={k.month_to_date} forecast={k.forecast} budget={k.budget} />
        <div className="legend3"><span><i className="mtd" />Spent {short(k.month_to_date)}</span><span><i className="fc" />Forecast {short(k.forecast)}</span><span><i className="bud" />Budget {short(k.budget)}</span></div>
        <div className="days" title={`Day ${k.days_elapsed} of ${k.days_in_month}`}>
          {Array.from({ length: k.days_in_month }, (_, i) => <i key={i} className={i < k.days_elapsed ? "on" : ""} />)}
        </div>
      </div>
      {k.confidence && (
        <div className="card mp">
          <div className="mp-head"><span className="eyebrow">Forecast confidence</span><InfoTip text={k.confidence.formula} /></div>
          <div className="conf">
            <Ring value={k.confidence.score} size={92} tone={k.confidence.score >= 75 ? "good" : k.confidence.score >= 50 ? "warn" : "bad"}>
              <CountUp value={k.confidence.score} format={pctFmt} />
            </Ring>
            <div className="conf-parts">
              {Object.entries(k.confidence.parts).map(([a, b], i) => (
                <div key={a} className="hrow tight">
                  <span className="lbl">{PART[a] ?? a}</span>
                  <span className="track"><motion.i className={b >= 75 ? "good" : b >= 40 ? "warn" : "bad"} initial={{ width: 0 }} animate={{ width: `${b}%` }} transition={grow(0.2 + i * 0.06)} /></span>
                  <span className="amt">{Math.round(b)}%</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
      <div className="card mp">
        <div className="mp-head"><span className="eyebrow">Savings this month</span><InfoTip text={k.formulas.verified_savings} /></div>
        <div className="mp-row"><CountUp className="mp-num good" value={k.verified_savings} format={short} /><span className="dim">verified</span></div>
        <div className="sv">
          {[["Verified", k.verified_savings, "save"], ["Negotiated", k.negotiated_savings, "muted"], ["Identified", k.opportunity_estimated, "muted"]].map(([l, v, c], i) => {
            const m = Math.max(1, k.verified_savings, k.negotiated_savings, k.opportunity_estimated);
            return (
              <div key={l as string} className="hrow tight">
                <span className="lbl">{l}</span>
                <span className="track"><motion.i className={c as string} initial={{ width: 0 }} animate={{ width: `${Math.max(2, ((v as number) / m) * 100)}%` }} transition={grow(0.2 + i * 0.06)} /></span>
                <span className="amt">{short(v as number)}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function CostCenters({ units }: { units: Unit[] }) {
  return (
    <div className="ccs">
      {units.map((u, i) => (
        <div key={u.CostCenter} className="cc" title={`${u.CostCenter}: spent ${money(u.MonthToDate)}, forecast ${money(u.Forecast)}, budget ${u.Budget == null ? "none" : money(u.Budget)}`}>
          <span className="lbl">{u.CostCenter}</span>
          <Bullet mtd={u.MonthToDate} forecast={u.Forecast} budget={u.Budget} delay={0.05 * i} />
          <span className={`var ${u.Variance == null ? "" : u.Variance > 0 ? "bad" : "good"}`}>
            {u.VariancePct == null ? "no budget" : `${u.VariancePct > 0 ? "+" : ""}${u.VariancePct.toFixed(1)}%`}
          </span>
        </div>
      ))}
    </div>
  );
}

export function CommandView() {
  const { me, version, refresh } = useMe();
  const router = useRouter();
  const allowed = me.modules.includes("command");
  useEffect(() => {
    if (!allowed) router.replace(homeFor(me.modules));
  }, [allowed, me.modules, router]);
  const state = useApi<Command>(allowed ? "/command" : null, version);
  if (!allowed) return null;
  return (
    <div className="stack cc-page">
      <PeriodPanel />
      <Loading state={state}>
        {(k) => (
          <>
            {k.headline && <ThreeNumbers h={k.headline} />}
            <MonthPosition k={k} />
            <div className="grid g2w-r">
              <div className="card">
                <div className="card-head"><h2>Requires your attention</h2><Link className="small" href="/anomalies">All findings</Link></div>
                <Findings items={k.findings} onDecided={refresh} />
              </div>
              <div className="stack">
                <div className="card">
                  <div className="card-head"><h2>Spend by month</h2></div>
                  <BarChart labels={k.trend.map((t) => MON[+t.Month.slice(5, 7) - 1])} stacked height="short"
                    datasets={[{ label: "Cloud and SaaS", data: k.trend.map((t) => t.Cloud), backgroundColor: "#2f6bff" },
                      { label: "AI", data: k.trend.map((t) => t.AI), backgroundColor: "#8a5cff" }]} />
                </div>
                <div className="card">
                  <div className="card-head"><h2>Cost centers</h2><span className="legend3 inline"><span><i className="mtd" />Spent</span><span><i className="fc" />Forecast</span><span><i className="bud" />Budget</span></span></div>
                  <CostCenters units={k.units} />
                </div>
              </div>
            </div>
          </>
        )}
      </Loading>
    </div>
  );
}
