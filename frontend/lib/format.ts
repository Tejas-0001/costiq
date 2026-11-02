// Number formatting shared by tables, KPIs and charts (ported from the CostIQ web app).
export const PALETTE = ["#2f6bff", "#8a5cff", "#12a594", "#ff7a45", "#ffb020", "#00b8d9", "#8e8e93", "#e5484d"];

const MONEY =
  /cost|spend|billed|amount|savings|variance|exposure|budget|forecast|overage|shortfall|effective|invoiced|charge(total)?$|actual|todate|risk|return|fees|annualized|measurable|escalation|value|baseline|o?andm$|percustomer|perrequest|peroutcome|perstudy|equivalent|billing|deferred|amortization|balance|subsidy|burn$/i;

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
  return MONEY.test(c) && !/pct|rate|ratio|percent|tokens|quantity|count|credits$|days$|units$|months$|lines$|meters$|customers$|studies$/i.test(c);
}

export function label(s: string): string {
  return s.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2");
}
