"use client";
// Board Pack: thirteen sections for the board and audit committee, each computed from live CostIQ data and marked
// In CostIQ / Partial / Needs data. Printable as one document.
import { BarChart } from "@/components/charts";
import { Gate, useMe } from "@/components/shell";
import { Formula, Loading, RecordTable } from "@/components/ui";
import { useApi } from "@/lib/api";
import { compact, money } from "@/lib/format";

type Metric = { label: string; value: number | null; fmt: "money" | "money2" | "pct" | "count"; of?: number; note?: string; tone?: "good" | "bad" | null };
type Section = {
  n: number; key: string; type: "core" | "addition"; title: string; why: string; status: "live" | "partial" | "needs" | "error";
  gap?: string; metrics: Metric[]; table?: { columns: string[]; rows: Record<string, unknown>[] };
  trend?: { Month: string; Value: number }[]; trend_label?: string; formula?: string;
};
type Pack = { as_of: string; generated: string; sections: Section[]; caution: string };

const STATUS: Record<Section["status"], [string, string]> = {
  live: ["In CostIQ", "b-good"], partial: ["Partial", "b-warn"], needs: ["Needs data", "b-bad"], error: ["Error", "b-bad"],
};

function value(m: Metric) {
  if (m.value == null) return <span className="muted">Not connected</span>;
  const v = m.fmt === "money" ? compact(m.value) : m.fmt === "money2" ? money(m.value, 2) : m.fmt === "pct" ? `${m.value}%` : m.value.toLocaleString("en-US");
  return m.of != null ? <>{v} <span className="muted of">of {m.of}</span></> : v;
}

function SectionCard({ s }: { s: Section }) {
  const [label, cls] = STATUS[s.status];
  return (
    <section className="card bp-sec" id={`bp-${s.key}`}>
      <div className="bp-head">
        <h2><span className="bp-n">{s.n}</span>{s.title}</h2>
        <span className="bp-type">{s.type === "core" ? "Core" : "Addition"}</span>
        <span className={`badge ${cls}`}>{label}</span>
      </div>
      <p className="muted bp-why">{s.why}</p>
      {s.gap && <p className="bp-gap">{s.gap}</p>}
      {s.metrics.length > 0 && (
        <div className="bp-metrics">
          {s.metrics.map((m) => (
            <div key={m.label} className={`kpi ${m.tone || ""}`}>
              <div className="label">{m.label}</div>
              <div className="value">{value(m)}</div>
              {m.note && <div className="sub">{m.note}</div>}
            </div>
          ))}
        </div>
      )}
      {s.trend && s.trend.length > 0 && (
        <div className="bp-trend">
          <h3>{s.trend_label}</h3>
          <BarChart labels={s.trend.map((t) => t.Month)} datasets={[{ label: "Spend", data: s.trend.map((t) => t.Value) }]} height="short" />
        </div>
      )}
      {s.table && s.table.rows.length > 0 && <div className="bp-table"><RecordTable rows={s.table.rows} columns={s.table.columns} /></div>}
      {s.formula && <Formula>{s.formula}</Formula>}
    </section>
  );
}

export function BoardView() {
  const { version } = useMe();
  const st = useApi<Pack>("/board", version);
  return (
    <Gate module="board">
      <div className="stack bp">
        <div className="page-head">
          <h1>Board Pack</h1>
          <div className="row">
            {st.data && <span className="muted small">Data as of {st.data.as_of}</span>}
            <button className="btn" onClick={() => window.print()}>Print</button>
          </div>
        </div>
        <Loading state={st}>
          {(d) => (
            <>
              <div className="card flush">
                <div className="table-wrap">
                  <table className="bp-index">
                    <thead><tr><th>#</th><th>Type</th><th>Section</th><th>Why the board cares</th><th>Status</th></tr></thead>
                    <tbody>
                      {d.sections.map((s) => (
                        <tr key={s.key}>
                          <td className="num">{s.n}</td>
                          <td>{s.type === "core" ? "Core" : "Addition"}</td>
                          <td><a href={`#bp-${s.key}`}>{s.title}</a></td>
                          <td>{s.why}</td>
                          <td><span className={`badge ${STATUS[s.status][1]}`}>{STATUS[s.status][0]}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
              {d.sections.map((s) => <SectionCard key={s.key} s={s} />)}
              <p className="bp-gap">{d.caution}</p>
            </>
          )}
        </Loading>
      </div>
    </Gate>
  );
}
