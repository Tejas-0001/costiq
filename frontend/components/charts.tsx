"use client";
import { useMemo } from "react";
import {
  BarElement, CategoryScale, Chart as ChartJS, Filler, Legend, LinearScale, LineElement, PointElement, Tooltip,
  type ChartOptions,
} from "chart.js";
import { Bar, Line } from "react-chartjs-2";
import { isMoneyCol, money, PALETTE } from "@/lib/format";

ChartJS.register(BarElement, CategoryScale, LinearScale, LineElement, PointElement, Tooltip, Legend, Filler);
ChartJS.defaults.font.family = "'Inter Variable', -apple-system, 'Segoe UI', system-ui, sans-serif";
ChartJS.defaults.font.size = 12;
ChartJS.defaults.color = "#8e8e93";
ChartJS.defaults.borderColor = "rgba(142, 142, 147, 0.16)";
ChartJS.defaults.animation = { duration: 900, easing: "easeOutQuart" };
ChartJS.defaults.elements.bar.borderRadius = 6;
ChartJS.defaults.elements.bar.borderSkipped = "start";
ChartJS.defaults.datasets.bar.maxBarThickness = 64;
ChartJS.defaults.plugins.legend.labels.usePointStyle = true;
ChartJS.defaults.plugins.legend.labels.pointStyle = "circle";
ChartJS.defaults.plugins.legend.labels.boxWidth = 8;
ChartJS.defaults.plugins.legend.labels.boxHeight = 8;
ChartJS.defaults.plugins.tooltip.backgroundColor = "rgba(29, 29, 31, 0.92)";
ChartJS.defaults.plugins.tooltip.padding = 10;
ChartJS.defaults.plugins.tooltip.cornerRadius = 10;
ChartJS.defaults.plugins.tooltip.titleFont = { weight: 600 };
ChartJS.defaults.plugins.tooltip.boxPadding = 4;

type Dataset = { label: string; data: (number | null)[]; backgroundColor?: string | string[]; borderColor?: string; type?: "line" | "bar";
  pointRadius?: number | number[]; pointBackgroundColor?: string | string[]; borderWidth?: number; fill?: boolean; stack?: string };

const moneyTick = (v: number | string) => money(Number(v));
/** Long category labels are cut to one short line; the tooltip still shows the full label. */
const clip = (l: string | undefined) => (l && l.length > 30 ? `${l.slice(0, 29)}…` : l ?? "");

/** Bar chart (vertical or horizontal, optionally stacked). Values are money unless `plain`. */
export function BarChart({ labels, datasets, horizontal, stacked, plain, height = "", onClick }: {
  labels: string[]; datasets: Dataset[]; horizontal?: boolean; stacked?: boolean; plain?: boolean; height?: "" | "tall" | "short";
  onClick?: (label: string) => void;
}) {
  const tick = plain ? (v: number | string) => Number(v).toLocaleString("en-US") : moneyTick;
  const opts: ChartOptions<"bar"> = {
    responsive: true, maintainAspectRatio: false, indexAxis: horizontal ? "y" : "x",
    plugins: {
      legend: { display: datasets.length > 1, position: "bottom" },
      tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${tick(c.parsed[horizontal ? "x" : "y"] ?? 0)}` } },
    },
    scales: horizontal
      ? { x: { stacked, ticks: { callback: tick, maxRotation: 0 } }, y: { stacked, ticks: { callback: (_v, i) => clip(labels[i]) } } }
      : { x: { stacked }, y: { stacked, ticks: { callback: tick } } },
    onClick: onClick ? (_e, els) => { if (els[0]) onClick(labels[els[0].index]); } : undefined,
  };
  const data = { labels, datasets: datasets.map((d, i) => ({ backgroundColor: PALETTE[i % PALETTE.length], ...d })) };
  return <div className={`chart-box ${height}`}><Bar data={data as never} options={opts} /></div>;
}

/** Line chart for daily or monthly series; `marks` highlights points (e.g. anomalies). */
export function LineChart({ labels, datasets, height = "", plain }: { labels: string[]; datasets: Dataset[]; height?: "" | "tall" | "short"; plain?: boolean }) {
  const tick = plain ? (v: number | string) => Number(v).toLocaleString("en-US") : moneyTick;
  const opts: ChartOptions<"line"> = {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: datasets.length > 1, position: "bottom" },
      tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${tick(c.parsed.y ?? 0)}` } } },
    scales: { y: { ticks: { callback: tick } }, x: { ticks: { maxTicksLimit: 12 } } },
    elements: { line: { tension: 0.25 } },
  };
  const data = { labels, datasets: datasets.map((d, i) => ({ borderColor: PALETTE[i % PALETTE.length], backgroundColor: PALETTE[i % PALETTE.length], pointRadius: 0, borderWidth: 2, ...d })) };
  return <div className={`chart-box ${height}`}><Line data={data as never} options={opts} /></div>;
}

/** Floating-bar waterfall: stages as bars from 0 (or a running base). Used for the savings lifecycle. */
export function Waterfall({ steps, height = "" }: { steps: { label: string; value: number; tone?: "good" | "bad" | "base" }[]; height?: "" | "tall" | "short" }) {
  const color = (t?: string) => (t === "good" ? "#20b26b" : t === "bad" ? "#ff5f45" : "#2f6bff");
  const data = {
    labels: steps.map((s) => s.label),
    datasets: [{ label: "Monthly savings", data: steps.map((s) => [0, s.value]), backgroundColor: steps.map((s) => color(s.tone)), borderRadius: 4 }],
  };
  const opts: ChartOptions<"bar"> = {
    responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => money((c.raw as number[])[1]) } } },
    scales: { y: { beginAtZero: true, ticks: { callback: moneyTick } } },
  };
  return <div className={`chart-box ${height}`}><Bar data={data as never} options={opts} /></div>;
}

/** Picks a sensible chart for any use-case result (ported from the CostIQ web app). */
export function AutoChart({ columns, rows }: { columns: string[]; rows: unknown[][] }) {
  const spec = useMemo(() => {
    if (!rows.length || rows.length > 2000) return null;
    const isNum = (i: number) => rows.every((r) => r[i] == null || typeof r[i] === "number") && rows.some((r) => typeof r[i] === "number");
    const nums = columns.map((_, i) => i).filter(isNum).filter((i) => !/^(seq|year|units|months|days)$|id$|pct$/i.test(columns[i]));
    if (!nums.length) return null;
    const pri = [/risk|effective|total.*cost|billed|cost|spend|amount|fees|return/i, /variance|savings|exposure/i, /tokens|credits|quantity/i];
    const y = pri.map((p) => nums.find((i) => p.test(columns[i]) && !/pct|rate|ratio|per/i.test(columns[i]))).find((i) => i != null) ?? nums[nums.length - 1];
    const texts = columns.map((_, i) => i).filter((i) => !nums.includes(i));
    const t = texts.find((i) => rows.every((r) => typeof r[i] === "string" && /^\d{4}-\d{2}/.test(r[i] as string)));
    if (t != null) {
      const series = texts.find((i) => i !== t && new Set(rows.map((r) => r[i])).size <= 8 && new Set(rows.map((r) => r[i])).size > 1);
      const xs = [...new Set(rows.map((r) => String(r[t]).slice(0, 10)))].sort();
      const groups = series != null ? [...new Set(rows.map((r) => r[series]))] : [columns[y]];
      const datasets = groups.map((g, gi) => ({
        label: String(g), backgroundColor: PALETTE[gi % PALETTE.length], borderColor: PALETTE[gi % PALETTE.length],
        data: xs.map((x) => rows.filter((r) => String(r[t]).slice(0, 10) === x && (series == null || r[series] === g))
          .reduce((a, r) => a + (Number(r[y]) || 0), 0)),
      }));
      return { kind: xs.length > 20 ? "line" : "bar", labels: xs, datasets, stacked: datasets.length > 1, yLabel: columns[y] };
    }
    const lab = texts[0];
    if (lab == null) return null;
    const agg = new Map<string, number>();
    rows.forEach((r) => agg.set(String(r[lab] ?? "—"), (agg.get(String(r[lab] ?? "—")) || 0) + (Number(r[y]) || 0)));
    const top = [...agg.entries()].sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 12);
    if (top.length < 2) return null;
    return { kind: "hbar", labels: top.map((e) => e[0]), datasets: [{ label: columns[y], data: top.map((e) => e[1]), backgroundColor: PALETTE[0] }], stacked: false, yLabel: columns[y] };
  }, [columns, rows]);
  if (!spec) return null;
  const plain = !isMoneyCol(spec.yLabel);
  if (spec.kind === "line") return <LineChart labels={spec.labels} datasets={spec.datasets} plain={plain} />;
  return <BarChart labels={spec.labels} datasets={spec.datasets} horizontal={spec.kind === "hbar"} stacked={spec.stacked} plain={plain} />;
}
