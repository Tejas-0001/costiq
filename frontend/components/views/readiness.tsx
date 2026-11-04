"use client";
// Data Readiness & Accounting Discovery (/readiness): the readiness scorecard for the selected period, the five data
// sections behind it, then the platform checklist and the four-week Discovery plan. Every label is tenant copy
// (readiness.*); the API (/api/readiness) returns keys and numbers only.
import Link from "next/link";
import "@/app/readiness.css";
import { useCopy } from "@/components/copy";
import { Gate, useMe, type PeriodInfo } from "@/components/shell";
import { Formula, Head, Kpi, Loading, StatusBadge } from "@/components/ui";
import { useApi } from "@/lib/api";
import { money } from "@/lib/format";

type SectionKey = "sources" | "tags" | "accounting" | "reconciliation" | "controls";
type Score = { key: SectionKey; score: number | null; status: string };
type Source = { Source: string; Provider: string | null; Format: string | null; FocusVersion: string | null; InvoiceIdPresentPct: number | null;
  ExportMode: string | null; MonthsAvailable: number; Rows: number; ManifestRows: number | null; RowsMatch: boolean;
  CipFlaggedSpend: number | null; Spend: number };
type TagKey = { TagKey: string; Requirement?: string; TaggableSpend: number; ValidSpend: number; UntaggedSpend: number; InvalidSpend: number;
  CoveragePct: number | null; Status?: string };
type Coverage = { TaggableSpend: number; ValidSpend: number; CoveragePct: number | null; Entity?: string; BusinessUnit?: string };
type Tags = { available: boolean; keys?: TagKey[]; by_entity?: Coverage[]; by_business_unit?: Coverage[];
  cannot_be_tagged?: { amount: number; by_category?: { Category: string; Amount: number }[] };
  suspense?: { amount: number; share_pct: number | null; lines: number | null; owner: string | null; oldest_days: number | null } };
type Rule = { RuleId: string; Basis: string | null; Status: string | null; Lines: number; AllocatedCost: number };
type Accounting = { total: number; mapped: number; unmapped: number; regulation: number | null; policy_signed_off: number | null;
  policy_awaiting: number | null; no_rule: number | null; by_rule: Rule[]; rules_available: boolean; capitalized: number | null;
  capitalized_valid_wo: number | null; work_orders: number | null; work_orders_with_threshold: number | null };
type Recon = { invoices: number; tied: number; labeled: number; tied_pct: number | null; periods: string[];
  variance_by_reason: { Reason: string; Status: string; Lines: number; Amount: number }[] };
type Control = { id: string; control: string; result: string; evidence: string };
type Readiness = { period: PeriodInfo; score: { overall: number | null; status: string; sections: Score[] }; sources: Source[]; tags: Tags;
  accounting: Accounting; reconciliation: Recon; controls: Control[]; confidence: { score: number; formula: string }; formula: string };

const BADGE: Record<string, string> = { ready: "b-good", partial: "b-warn", gaps: "b-bad", na: "b-info" };
const pctOf = (part: number | null | undefined, whole: number | null | undefined) =>
  part == null || !whole ? null : Math.round((1000 * part) / whole) / 10;
const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(1)}%`);

function Meter({ value, tone }: { value: number | null; tone?: string }) {
  const w = value == null ? 0 : Math.max(0, Math.min(100, value));
  return <span className="rd-meter" aria-hidden="true"><i className={tone ?? ""} style={{ width: `${w}%` }} /></span>;
}

export function ReadinessView() {
  const { period, version } = useMe();
  const { t, get } = useCopy();
  const state = useApi<Readiness>(`/readiness?range=${period}`, version);
  const status = (s: string) => <span className={`badge ${BADGE[s] ?? "b-info"}`}>{t(`readiness.status.${s}`, undefined, s)}</span>;
  const yes = (b: boolean) => (b ? t("readiness.sources.yes") : t("readiness.sources.no"));
  return (
    <Gate module="readiness">
      <Head title={t("readiness.title")}>{state.data && <span className="pill">{state.data.period.label}</span>}</Head>
      <Loading state={state}>
        {(r) => {
          const a = r.accounting;
          const tags = r.tags;
          const flagged = r.sources.some((s) => s.CipFlaggedSpend != null);
          const basis: [string, number | null, string][] = a.rules_available
            ? [["regulation", a.regulation, ""], ["policySigned", a.policy_signed_off, ""], ["policyAwaiting", a.policy_awaiting, "warn"],
              ["noRule", a.no_rule, "bad"], ["unmapped", a.unmapped, "bad"]]
            : [["mapped", a.mapped, ""], ["unmapped", a.unmapped, "bad"]];
          const weeks = get<string[][]>("readiness.discovery.weeks") ?? [];
          const inputs = get<string[][]>("readiness.discovery.inputs") ?? [];
          const items = get<string[][]>("readiness.checklist.items") ?? [];
          const list = (key: string) => (get<string[]>(key) ?? []).map((x) => <li key={x}>{x}</li>);
          return (
            <div className="stack rd">
              {/* scorecard */}
              <div className="card flush">
                <div className="card-head"><h2>{t("readiness.scorecard.title")}</h2><span className="small muted">{r.period.long}</span></div>
                <div className="table-wrap"><table>
                  <thead><tr><th>{t("readiness.scorecard.section")}</th><th className="rd-score-col">{t("readiness.scorecard.score")}</th>
                    <th>{t("readiness.scorecard.status")}</th><th>{t("readiness.scorecard.measures")}</th></tr></thead>
                  <tbody>
                    {r.score.sections.map((s) => (
                      <tr key={s.key}>
                        <td><a href={`#rd-${s.key}`}>{t(`readiness.sections.${s.key}.label`)}</a></td>
                        <td className="rd-score"><Meter value={s.score} tone={s.status} /><b>{s.score == null ? "—" : s.score.toFixed(1)}</b></td>
                        <td>{status(s.status)}</td>
                        <td className="small">{t(`readiness.sections.${s.key}.measures`)}</td>
                      </tr>
                    ))}
                    <tr className="total">
                      <td>{t("readiness.scorecard.overall")}</td>
                      <td className="rd-score"><Meter value={r.score.overall} tone={r.score.status} /><b>{r.score.overall == null ? "—" : r.score.overall.toFixed(1)}</b></td>
                      <td>{status(r.score.status)}</td><td />
                    </tr>
                  </tbody>
                </table></div>
              </div>

              {/* sources */}
              <div className="card flush" id="rd-sources">
                <div className="card-head"><h2>{t("readiness.sections.sources.label")}</h2>{status(r.score.sections.find((s) => s.key === "sources")?.status ?? "na")}</div>
                <div className="table-wrap"><table>
                  <thead><tr>
                    {["Source", "Provider", "Format", "FocusVersion"].map((c) => <th key={c}>{t(`readiness.sources.${c}`)}</th>)}
                    <th className="num">{t("readiness.sources.InvoiceIdPresentPct")}</th><th>{t("readiness.sources.ExportMode")}</th>
                    {["MonthsAvailable", "Rows", "ManifestRows"].map((c) => <th key={c} className="num">{t(`readiness.sources.${c}`)}</th>)}
                    <th>{t("readiness.sources.RowsMatch")}</th>
                    {flagged && <th className="num">{t("readiness.sources.CipFlaggedSpend")}</th>}
                  </tr></thead>
                  <tbody>{r.sources.map((s) => (
                    <tr key={s.Source}>
                      <td className="id"><b>{s.Source}</b></td><td>{s.Provider ?? "—"}</td><td className="small">{s.Format ?? "—"}</td>
                      <td>{s.FocusVersion ?? "—"}</td><td className="num">{fmtPct(s.InvoiceIdPresentPct)}</td><td className="small">{s.ExportMode ?? "—"}</td>
                      <td className="num">{s.MonthsAvailable}</td><td className="num">{s.Rows.toLocaleString("en-US")}</td>
                      <td className="num">{s.ManifestRows == null ? "—" : s.ManifestRows.toLocaleString("en-US")}</td>
                      <td><span className={`badge ${s.RowsMatch ? "b-good" : "b-bad"}`}>{yes(s.RowsMatch)}</span></td>
                      {flagged && <td className="num">{s.CipFlaggedSpend ? money(s.CipFlaggedSpend, 2) : "—"}</td>}
                    </tr>))}
                    <tr className="total">
                      <td>{t("readiness.sources.total")}</td><td colSpan={5} />
                      <td /><td className="num">{r.sources.reduce((n, s) => n + s.Rows, 0).toLocaleString("en-US")}</td>
                      <td className="num">{r.sources.reduce((n, s) => n + (s.ManifestRows ?? 0), 0).toLocaleString("en-US")}</td><td />
                      {flagged && <td className="num">{money(r.sources.reduce((n, s) => n + (s.CipFlaggedSpend ?? 0), 0), 2)}</td>}
                    </tr>
                  </tbody>
                </table></div>
              </div>

              {/* tags */}
              <div className="card" id="rd-tags">
                <div className="card-head"><h2>{t("readiness.sections.tags.label")}</h2>
                  <span className="row">{status(r.score.sections.find((s) => s.key === "tags")?.status ?? "na")}
                    <Link className="small" href="/allocation">{t("readiness.tags.link")}</Link></span></div>
                {!tags.available ? <div className="notice">{t("readiness.tags.none")}</div> : (
                  <div className="stack">
                    <div className="grid g3">
                      <Kpi label={t("readiness.tags.suspense")} value={money(tags.suspense?.amount ?? 0, 2)} tone={(tags.suspense?.amount ?? 0) > 0 ? "bad" : "good"}
                        sub={<>{tags.suspense?.share_pct != null && t("readiness.tags.suspenseSub", { share: tags.suspense.share_pct, owner: tags.suspense.owner ?? "—" })}
                          {tags.suspense?.oldest_days != null && <div>{t("readiness.tags.oldest", { days: tags.suspense.oldest_days })}</div>}</>} />
                      <Kpi label={t("readiness.tags.cannotTag")} value={money(tags.cannot_be_tagged?.amount ?? 0, 2)} sub={t("readiness.tags.cannotTagSub")} />
                      <Kpi label={t("readiness.sections.tags.label")} value={fmtPct(r.score.sections.find((s) => s.key === "tags")?.score)}
                        sub={t("readiness.sections.tags.measures")} />
                    </div>
                    <div className="table-wrap"><table>
                      <thead><tr><th>{t("readiness.tags.TagKey")}</th><th>{t("readiness.tags.Requirement")}</th><th className="rd-score-col">{t("readiness.tags.CoveragePct")}</th>
                        <th className="num">{t("readiness.tags.ValidSpend")}</th><th className="num">{t("readiness.tags.UntaggedSpend")}</th><th className="num">{t("readiness.tags.InvalidSpend")}</th></tr></thead>
                      <tbody>{(tags.keys ?? []).map((k) => (
                        <tr key={k.TagKey}><td className="id"><b>{k.TagKey}</b></td><td>{k.Requirement ?? "—"}</td>
                          <td className="rd-score"><Meter value={k.CoveragePct} tone={(k.CoveragePct ?? 0) >= 99 ? "ready" : (k.CoveragePct ?? 0) >= 80 ? "partial" : "gaps"} />{fmtPct(k.CoveragePct)}</td>
                          <td className="num">{money(k.ValidSpend, 2)}</td><td className="num">{money(k.UntaggedSpend, 2)}</td><td className="num">{money(k.InvalidSpend, 2)}</td></tr>))}
                      </tbody>
                    </table></div>
                    {(tags.by_entity?.length || tags.by_business_unit?.length) ? (
                      <div className="grid g2">
                        {[["byEntity", "entity", tags.by_entity ?? [], "Entity"], ["byUnit", "unit", tags.by_business_unit ?? [], "BusinessUnit"]].map(([title, col, rows, key]) => (
                          <div key={title as string}><h3>{t(`readiness.tags.${title}`)}</h3>
                            <div className="table-wrap"><table>
                              <thead><tr><th>{t(`readiness.tags.${col}`)}</th><th className="num">{t("readiness.tags.taggable")}</th><th className="rd-score-col">{t("readiness.tags.CoveragePct")}</th></tr></thead>
                              <tbody>{(rows as Coverage[]).map((x) => {
                                const name = String(x[key as "Entity" | "BusinessUnit"] ?? "—");
                                return <tr key={name}><td>{name}</td><td className="num">{money(x.TaggableSpend, 2)}</td>
                                  <td className="rd-score"><Meter value={x.CoveragePct} />{fmtPct(x.CoveragePct)}</td></tr>;
                              })}</tbody>
                            </table></div>
                          </div>))}
                      </div>) : null}
                  </div>)}
              </div>

              {/* accounting */}
              <div className="card" id="rd-accounting">
                <div className="card-head"><h2>{t("readiness.sections.accounting.label")}</h2>
                  <span className="row">{status(r.score.sections.find((s) => s.key === "accounting")?.status ?? "na")}
                    {a.rules_available && <Link className="small" href="/tax?tab=rules">{t("readiness.accounting.link")}</Link>}</span></div>
                <div className="grid g2w">
                  <div className="stack">
                    <div>
                      <h3>{t("readiness.accounting.byBasis")} <span className="small muted">· {t("readiness.accounting.total")} {money(a.total, 2)}</span></h3>
                      {basis.map(([k, v, tone]) => (
                        <div key={k} className="hrow rd-hrow">
                          <span className="lbl">{t(`readiness.accounting.${k}`)}</span>
                          <span className="track"><i className={tone ? `rd-${tone}` : ""} style={{ width: `${Math.max(0, Math.min(100, pctOf(v, a.total) ?? 0))}%` }} /></span>
                          <span className="amt">{fmtPct(pctOf(v, a.total))}</span>
                          <span className="small muted rd-amt">{money(v ?? 0, 2)}</span>
                        </div>))}
                      {!a.rules_available && <p className="small muted">{t("readiness.accounting.noRules")}</p>}
                    </div>
                    {a.capitalized != null && (
                      <div className="grid g2">
                        <Kpi label={t("readiness.accounting.capital")} value={fmtPct(pctOf(a.capitalized_valid_wo, a.capitalized))}
                          tone={(a.capitalized_valid_wo ?? 0) < (a.capitalized ?? 0) ? "bad" : "good"}
                          sub={t("readiness.accounting.capitalSub", { valid: money(a.capitalized_valid_wo, 2), total: money(a.capitalized, 2) })} />
                        <Kpi label={t("readiness.accounting.workOrders")} value={`${a.work_orders_with_threshold ?? 0} / ${a.work_orders ?? 0}`}
                          tone={(a.work_orders_with_threshold ?? 0) < (a.work_orders ?? 0) ? "bad" : "good"}
                          sub={t("readiness.accounting.workOrdersSub", { with: a.work_orders_with_threshold ?? 0, all: a.work_orders ?? 0 })} />
                      </div>)}
                  </div>
                  {a.rules_available ? (
                    <div><h3>{t("readiness.accounting.byRule")}</h3>
                      <div className="table-wrap"><table>
                        <thead><tr><th>{t("readiness.accounting.RuleId")}</th><th>{t("readiness.accounting.Basis")}</th><th>{t("readiness.accounting.Status")}</th>
                          <th className="num">{t("readiness.accounting.Lines")}</th><th className="num">{t("readiness.accounting.AllocatedCost")}</th></tr></thead>
                        <tbody>{a.by_rule.map((x) => (
                          <tr key={x.RuleId ?? "none"}><td className="id"><b>{x.RuleId ?? "—"}</b></td>
                            <td>{String(x.Basis ?? "").toUpperCase() === "POLICY" ? "[POLICY]" : x.Basis ?? "—"}</td>
                            <td>{x.Status == null ? "—" : x.Status === "approved"
                              ? <span className="badge b-good">{t("readiness.accounting.signed")}</span>
                              : <span className="badge b-warn">{t("readiness.accounting.awaiting")}</span>}</td>
                            <td className="num">{x.Lines.toLocaleString("en-US")}</td><td className="num">{money(x.AllocatedCost, 2)}</td></tr>))}
                        </tbody>
                      </table></div>
                    </div>) : <div />}
                </div>
              </div>

              {/* reconciliation */}
              <div className="card" id="rd-reconciliation">
                <div className="card-head"><h2>{t("readiness.sections.reconciliation.label")}</h2>
                  <span className="row">{status(r.score.sections.find((s) => s.key === "reconciliation")?.status ?? "na")}
                    <Link className="small" href="/reconciliation">{t("readiness.reconciliation.link")}</Link></span></div>
                <div className="grid g2w">
                  <div className="grid g3 rd-kpis">
                    <Kpi label={t("readiness.reconciliation.invoices")} value={r.reconciliation.invoices}
                      sub={t("readiness.reconciliation.invoicesSub", { periods: r.reconciliation.periods.join(", ") || "—" })} />
                    <Kpi label={t("readiness.reconciliation.tied")} value={r.reconciliation.tied} tone="good" sub={fmtPct(r.reconciliation.tied_pct)} />
                    <Kpi label={t("readiness.reconciliation.labeled")} value={r.reconciliation.labeled}
                      sub={fmtPct(pctOf(r.reconciliation.labeled, r.reconciliation.invoices))} />
                  </div>
                  <div><h3>{t("readiness.reconciliation.byReason")}</h3>
                    {r.reconciliation.variance_by_reason.length === 0 ? <p className="small muted">{t("readiness.reconciliation.none")}</p> : (
                      <div className="table-wrap"><table>
                        <thead><tr><th>{t("readiness.reconciliation.Reason")}</th><th className="num">{t("readiness.reconciliation.Lines")}</th>
                          <th className="num">{t("readiness.reconciliation.Amount")}</th></tr></thead>
                        <tbody>{r.reconciliation.variance_by_reason.map((v) => (
                          <tr key={v.Reason}><td>{v.Reason} <StatusBadge s={v.Status} /></td><td className="num">{v.Lines}</td><td className="num">{money(v.Amount, 2)}</td></tr>))}
                        </tbody>
                      </table></div>)}
                  </div>
                </div>
              </div>

              {/* controls */}
              <div className="card flush" id="rd-controls">
                <div className="card-head"><h2>{t("readiness.sections.controls.label")}</h2>
                  <span className="row">{status(r.score.sections.find((s) => s.key === "controls")?.status ?? "na")}
                    <span className="pill" title={r.confidence.formula}>{t("readiness.controls.confidence")} <b>{r.confidence.score.toFixed(1)}</b></span>
                    <Link className="small" href="/governance">{t("readiness.controls.link")}</Link></span></div>
                <div className="table-wrap"><table>
                  <thead><tr><th>{t("readiness.controls.id")}</th><th>{t("readiness.controls.control")}</th><th>{t("readiness.controls.result")}</th>
                    <th>{t("readiness.controls.evidence")}</th></tr></thead>
                  <tbody>{r.controls.map((c) => (
                    <tr key={c.id}><td className="uc-id">{c.id}</td><td>{c.control}</td><td><StatusBadge s={c.result} /></td><td className="small">{c.evidence}</td></tr>))}
                  </tbody>
                </table></div>
              </div>

              {/* platform checklist (answered in Discovery) */}
              <div className="card flush">
                <div className="card-head"><h2>{t("readiness.checklist.title")}</h2></div>
                <div className="table-wrap"><table>
                  <thead><tr><th>{t("readiness.checklist.item")}</th><th>{t("readiness.checklist.need")}</th><th>{t("readiness.checklist.state")}</th></tr></thead>
                  <tbody>{items.map(([item, need]) => (
                    <tr key={item}><td><b>{item}</b></td><td className="small">{need}</td><td><span className="badge b-info">{t("readiness.checklist.toConfirm")}</span></td></tr>))}
                  </tbody>
                </table></div>
              </div>

              {/* Discovery plan */}
              <div className="card rd-discovery">
                <h2>{t("readiness.discovery.title")}</h2>
                <div className="grid g2w">
                  <div className="table-wrap"><table>
                    <thead><tr><th>{t("readiness.discovery.week")}</th><th>{t("readiness.discovery.work")}</th></tr></thead>
                    <tbody>{weeks.map(([w, work]) => <tr key={w}><td className="rd-week">{w}</td><td>{work}</td></tr>)}</tbody>
                  </table></div>
                  <div className="stack rd-lists">
                    <div><h3>{t("readiness.discovery.outputsTitle")}</h3><ul>{list("readiness.discovery.outputs")}</ul></div>
                    <div><h3>{t("readiness.discovery.criteriaTitle")}</h3><ul>{list("readiness.discovery.criteria")}</ul></div>
                    <div><h3>{t("readiness.discovery.decisionsTitle")}</h3><ul>{list("readiness.discovery.decisions")}</ul></div>
                  </div>
                </div>
                <h3 className="rd-inputs-title">{t("readiness.discovery.inputsTitle")}</h3>
                <div className="table-wrap"><table>
                  <thead><tr><th className="num">{t("readiness.discovery.number")}</th><th>{t("readiness.discovery.input")}</th><th>{t("readiness.discovery.form")}</th></tr></thead>
                  <tbody>{inputs.map(([n, item, form]) => <tr key={n}><td className="num">{n}</td><td>{item}</td><td className="small">{form}</td></tr>)}</tbody>
                </table></div>
              </div>

              <Formula>{r.formula}</Formula>
            </div>
          );
        }}
      </Loading>
    </Gate>
  );
}
