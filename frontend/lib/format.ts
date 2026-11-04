// Number formatting shared by tables, KPIs and charts (ported from the CostIQ web app).
// Column patterns that depend on the tenant's own columns come from its copy (format.*), read after rememberCopy().
import { copyText, copyValue } from "@/components/copy";

export const PALETTE = ["#0f6ec8", "#454d53", "#97b5c3", "#ca4528", "#2e7d6b", "#b8c1cb", "#0a4a87", "#d6d6d6"];

const MONEY =
  "cost|spend|billed|amount|savings|variance|exposure|budget|forecast|overage|shortfall|effective|invoiced|charge(total)?$|actual|todate|risk|return|fees|annualized|measurable|escalation|value|baseline|o?andm$|perrequest|peroutcome|equivalent|billing|credit$|balance|burn$";
const NOT_MONEY = "pct|rate|ratio|percent|tokens|quantity|count|credits$|days$|units$|months$|lines$";

let patterns: { key: string; money: RegExp; notMoney: RegExp } | null = null;

/** The money / not-money column patterns: the shared ones plus the tenant's (format.moneyColumns, format.notMoneyColumns). */
function columnPatterns() {
  const m = copyText("format.moneyColumns"), n = copyText("format.notMoneyColumns"), key = `${m}\n${n}`;
  if (!patterns || patterns.key !== key) {
    patterns = { key, money: new RegExp(m ? `${MONEY}|${m}` : MONEY, "i"), notMoney: new RegExp(n ? `${NOT_MONEY}|${n}` : NOT_MONEY, "i") };
  }
  return patterns;
}

export function money(v: unknown, d = 0): string {
  const n = typeof v === "number" ? v : v == null ? NaN : Number(v);
  if (Number.isNaN(n)) return "—";
  return `${n < 0 ? "−" : ""}$${Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d })}`;
}

/** $1.2M / $268K style for headline numbers. */
export function compact(v: unknown): string {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return "—";
  const a = Math.abs(n);
  const s = a >= 1e6 ? `${(a / 1e6).toFixed(2)}M` : a >= 1e3 ? `${(a / 1e3).toFixed(0)}K` : a.toFixed(0);
  return `${n < 0 ? "−" : ""}$${s}`;
}

export function num(v: unknown): string {
  if (v == null) return "—";
  return typeof v === "number" ? v.toLocaleString("en-US", { maximumFractionDigits: 4 }) : String(v);
}

export function pct(v: unknown, signed = true): string {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  return `${signed && n > 0 ? "+" : ""}${n.toFixed(1)}%`;
}

export function isMoneyCol(c: string): boolean {
  const p = columnPatterns();
  return p.money.test(c) && !p.notMoney.test(c);
}

export function label(s: string): string {
  return s.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2");
}

/** A table column's header: the tenant's own name for a shared column (format.columnLabels), else label(). */
export function columnLabel(c: string): string {
  const own = (copyValue<[string, string][]>("format.columnLabels") ?? []).find(([k]) => k === c);
  return own ? own[1] : label(c);
}
