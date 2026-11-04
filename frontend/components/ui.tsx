"use client";
import { useMemo, type ReactNode } from "react";
import type { ApiState } from "@/lib/api";
import { isMoneyCol, label, money, num } from "@/lib/format";

export function Head({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="page-head">
      <h1>{title}</h1>
      {children && <div className="row">{children}</div>}
    </div>
  );
}

export function Loading<T>({ state, children }: { state: ApiState<T>; children: (data: T) => ReactNode }) {
  if (state.error) return <div className="error">{state.error.message}</div>;
  if (state.loading && !state.data) return <div className="muted small">Loading…</div>;
  if (!state.data) return null;
  return <>{children(state.data)}</>;
}

/** One-line method note under a section. */
export function Formula({ children }: { children: ReactNode }) {
  return <p className="foot">{children}</p>;
}

export function Kpi({ label, value, sub, formula, tone }: { label: string; value: ReactNode; sub?: ReactNode; formula?: string; tone?: "good" | "bad" | "" }) {
  return (
    <div className={`card kpi ${tone || ""}`} title={formula}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

export function StatusBadge({ s }: { s: unknown }) {
  const v = String(s ?? "").toLowerCase();
  const cls = /over|critical|exception|fail|high|unused|not approved|unresolved|overdue|no approved|without|differs|untagged|unmapped|missing|provider can decrypt|provider-managed keys|no benefit|exhausted|run out|under-utilized|idle|evidence gaps|rejected|not yet requested|attention|not reconciled/.test(v)
    ? "b-bad"
    : /^(approved|approved policy|mapped|complete|tied to invoices|claimed|verified|customer-managed keys|clauses in place|provisioning justified|stable)$/.test(v)
      ? "b-good"
      : /risk|warn|medium|within|open|provisional|identified|in forecast year|eligible|growing|routing|implemented|submitted|draft|review/.test(v)
        ? "b-warn"
        : /on track|under|pass|matched|ready|ok|low|final|approved|compliant/.test(v)
          ? "b-good"
          : "b-info";
  return <span className={`badge ${cls}`}>{String(s)}</span>;
}

const BADGE_COL = /status|finding|finality|readiness|tieout|reason|timing|opportunity|guidance/i;
// Identifiers and dates read badly when broken at hyphens.
const ID_COL = /filing|day$|date$|period|^seq$/i;

export function DataTable({ columns, rows, max = 500, onRow, total }: {
  columns: string[]; rows: unknown[][]; max?: number; onRow?: (r: unknown[]) => void; total?: unknown[];
}) {
  const numeric = useMemo(
    () => columns.map((_, i) => rows.length > 0 && rows.every((r) => r[i] == null || typeof r[i] === "number")),
    [columns, rows],
  );
  if (!rows.length) return <div className="muted small" style={{ padding: 12 }}>No rows in your scope for these parameters.</div>;
  const cell = (v: unknown, i: number) =>
    BADGE_COL.test(columns[i]) && v ? <StatusBadge s={v} />
      : numeric[i] ? (isMoneyCol(columns[i]) ? money(v, 2) : num(v))
        : typeof v === "boolean" ? (v ? "yes" : "no")
          : v != null && typeof v === "object" ? <span className="mono">{JSON.stringify(v)}</span>
            : String(v ?? "—");
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{columns.map((c, i) => <th key={c + i} className={numeric[i] ? "num" : ""}>{label(c)}</th>)}</tr>
        </thead>
        <tbody>
          {rows.slice(0, max).map((r, ri) => (
            <tr key={ri} onClick={onRow ? () => onRow(r) : undefined} style={onRow ? { cursor: "pointer" } : undefined}>
              {r.map((v, i) => <td key={i} className={numeric[i] ? "num" : ID_COL.test(columns[i]) ? "id" : ""}>{cell(v, i)}</td>)}
            </tr>
          ))}
          {total && (
            <tr className="total">{total.map((v, i) => <td key={i} className={numeric[i] ? "num" : ""}>{v == null ? "" : cell(v, i)}</td>)}</tr>
          )}
        </tbody>
      </table>
      {rows.length > max && <div className="muted small" style={{ padding: 8 }}>Showing {max} of {rows.length} rows. Export CSV for all.</div>}
    </div>
  );
}

/** Array of objects -> DataTable. */
export function RecordTable({ rows, columns, max, onRow, total }: {
  rows: Record<string, unknown>[]; columns?: string[]; max?: number; onRow?: (r: Record<string, unknown>) => void;
  total?: Record<string, unknown>;
}) {
  const cols = columns ?? (rows[0] ? Object.keys(rows[0]) : []);
  return (
    <DataTable columns={cols} rows={rows.map((r) => cols.map((c) => r[c]))} max={max}
      onRow={onRow ? (r) => onRow(Object.fromEntries(cols.map((c, i) => [c, r[i]]))) : undefined}
      total={total ? cols.map((c) => total[c]) : undefined} />
  );
}
