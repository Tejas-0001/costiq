"use client";
// Landing / sign-in page: what MM CostIQ is, live headline figures, and sign-in by role.
// The tenant's words (persona cards, groups, value tiles, band line and art) come from /api/config copy (landing.*).
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { choosePersona } from "@/app/actions";
import { MMLockup } from "@/components/brand";
import { lookup, type Copy } from "@/components/copy";
import { api } from "@/lib/api";

type Persona = { key: string; label: string; group: string };
type Highlights = {
  run_rate: number; spend_series: number[]; forecast_variance_pct: number; variance_series: number[];
  optimization_potential: number; optimization_series: number[]; as_of: string;
};
type Config = { auth_mode: string; personas: Persona[]; highlights: Highlights | null; tenant?: string; copy?: Copy };

/** The tenant's landing copy: groups [name, tone, icon, sub], persona cards [key, icon, description], value tiles
 *  [icon, title, text], the band's first line and the band art ("helix" or "grid"). */
function landingCopy(copy: Copy | undefined) {
  const c = copy ?? {};
  const groups = lookup<[string, string, string, string][]>(c, "landing.groups") ?? [];
  const personas = lookup<[string, string, string][]>(c, "landing.personas") ?? [];
  return {
    groups: Object.fromEntries(groups.map(([name, tone, icon, sub]) => [name, { tone, icon, sub }])),
    order: groups.map(([name]) => name),
    icon: Object.fromEntries(personas.map(([key, icon]) => [key, icon])) as Record<string, string>,
    description: Object.fromEntries(personas.map(([key, , d]) => [key, d])) as Record<string, string>,
    value: (lookup<[string, string, string][]>(c, "landing.value") ?? []).map(([icon, t, d]) => ({ icon, t, d })),
    band: lookup<string>(c, "landing.band") ?? "",
    art: lookup<string>(c, "landing.art") ?? "",
  };
}

const PATHS: Record<string, string> = {
  bars: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z",
  shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10zM9 12l2 2 4-4",
  crown: "M3 18h18M4 15l-1-9 5 4 4-6 4 6 5-4-1 9H4z",
  doc: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zM14 2v6h6M8 13h8M8 17h6",
  trend: "M3 17l6-6 4 4 8-8M15 7h6v6",
  monitor: "M3 4h18v12H3zM8 20h8M12 16v4",
  cloud: "M17.5 19H8a5 5 0 1 1 .9-9.9A6 6 0 0 1 20.5 11a4 4 0 0 1-3 8z",
  server: "M3 4h18v6H3zM3 14h18v6H3zM7 7h.01M7 17h.01",
  spark: "M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4z",
  calc: "M5 2h14v20H5zM8 6h8v4H8zM8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01M16 18h.01",
  search: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zM11.5 16a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM13.5 15.5L16 18",
  target: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  bolt: "M13 2L3 14h9l-1 8 10-12h-9l1-8z",
  chart: "M3 3v18h18M7 16V11M12 16V7M17 16v-3",
  up: "M3 17l6-6 4 4 8-8M15 7h6v6",
  lock: "M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4",
  chev: "M9 6l6 6-6 6",
  arrow: "M7 4v10a4 4 0 0 0 4 4h8M15 14l4 4-4 4",
};

function Icon({ name, size = 20, stroke = 1.8 }: { name: string; size?: number; stroke?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={stroke}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={PATHS[name]} /></svg>
  );
}

function fmtMoney(v: number | null | undefined) {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
}

function MiniBars({ values = [], color }: { values?: number[]; color: string }) {
  if (!values.length) return null;
  const max = Math.max(...values.map(Math.abs)) || 1;
  const w = 90, h = 40, bw = w / values.length - 4;
  return (
    <svg width={w} height={h} aria-hidden="true">
      {values.map((v, i) => {
        const bh = Math.max(3, (Math.abs(v) / max) * h);
        return <rect key={i} x={i * (bw + 4)} y={h - bh} width={bw} height={bh} rx="1.5" fill={color} opacity={0.55 + 0.45 * ((i + 1) / values.length)} />;
      })}
    </svg>
  );
}

function MiniArea({ values = [], color }: { values?: number[]; color: string }) {
  if (values.length < 2) return null;
  const w = 90, h = 40, min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const line = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - 4 - ((v - min) / span) * (h - 8)}`).join(" ");
  return (
    <svg width={w} height={h} aria-hidden="true">
      <polygon points={`0,${h} ${line} ${w},${h}`} fill={color} opacity="0.35" />
      <polyline points={line} fill="none" stroke="#d7e7f7" strokeWidth="1.6" />
    </svg>
  );
}

/** Abstract art (copy landing.art "helix"): a double helix, hexagonal rings and cell-like circles on a slate field. */
function HeroArt() {
  const W = 1280, H = 380, N = 64;
  const strand = (phase: number) => Array.from({ length: N + 1 }, (_, i) => {
    const x = 120 + (i / N) * 740, y = 190 + Math.sin((i / N) * Math.PI * 4 + phase) * 92;
    return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const rungs = Array.from({ length: 33 }, (_, i) => {
    const t = i / 32, x = 120 + t * 740;
    return { x, y1: 190 + Math.sin(t * Math.PI * 4) * 92, y2: 190 + Math.sin(t * Math.PI * 4 + Math.PI) * 92 };
  });
  const hex = (cx: number, cy: number, r: number) => Array.from({ length: 6 }, (_, k) => {
    const ang = Math.PI / 6 + (k * Math.PI) / 3;
    return `${(cx + r * Math.cos(ang)).toFixed(1)},${(cy + r * Math.sin(ang)).toFixed(1)}`;
  }).join(" ");
  const rings = [[1010, 120, 34], [1069, 154, 34], [1069, 222, 34], [1128, 120, 34], [180, 300, 22], [218, 322, 22]];
  const cells = [[80, 80, 46], [1180, 300, 70], [960, 320, 28], [330, 60, 18], [620, 330, 14]];
  return (
    <svg className="lp-art" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <defs>
        <radialGradient id="glow" cx="0.42" cy="0.5" r="0.6">
          <stop offset="0" stopColor="#0f6ec8" stopOpacity="0.5" /><stop offset="1" stopColor="#22282c" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="fade" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0.55" stopColor="#22282c" stopOpacity="0" /><stop offset="1" stopColor="#22282c" stopOpacity="0.85" />
        </linearGradient>
      </defs>
      <rect width={W} height={H} fill="#22282c" />
      <rect width={W} height={H} fill="url(#glow)" />
      {cells.map(([x, y, r], i) => <g key={i}><circle cx={x} cy={y} r={r} fill="#97b5c3" opacity="0.08" /><circle cx={x} cy={y} r={r * 0.35} fill="#97b5c3" opacity="0.12" /></g>)}
      {rungs.map((r, i) => <line key={i} x1={r.x} x2={r.x} y1={r.y1} y2={r.y2} stroke="#b8c1cb" strokeOpacity="0.28" strokeWidth="2" />)}
      <path d={strand(0)} fill="none" stroke="#6fb3e8" strokeWidth="3" strokeOpacity="0.9" />
      <path d={strand(Math.PI)} fill="none" stroke="#97b5c3" strokeWidth="3" strokeOpacity="0.7" />
      {rungs.filter((_, i) => i % 4 === 0).map((r, i) => <g key={i}><circle cx={r.x} cy={r.y1} r="5" fill="#bfe0f7" /><circle cx={r.x} cy={r.y2} r="4" fill="#97b5c3" /></g>)}
      {rings.map(([x, y, r], i) => <polygon key={i} points={hex(x, y, r)} fill="none" stroke="#6fb3e8" strokeOpacity="0.45" strokeWidth="2" />)}
      <rect width={W} height={H} fill="url(#fade)" />
    </svg>
  );
}

/** Abstract art (copy landing.art "grid"): a three-phase waveform strung between poles, and a small mesh of network nodes,
 *  on the same slate field and palette as HeroArt. */
function GridArt() {
  const W = 1280, H = 380, N = 120;
  const wave = (phase: number) => Array.from({ length: N + 1 }, (_, i) => {
    const x = 80 + (i / N) * 820, y = 210 + Math.sin((i / N) * Math.PI * 6 + phase) * 64;
    return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const poles = Array.from({ length: 7 }, (_, i) => 110 + i * 130);
  const span = (y: number, sag: number) => poles.slice(1).map((x, i) =>
    `M${poles[i]},${y} Q${(poles[i] + x) / 2},${y + sag} ${x},${y}`).join(" ");
  const nodes = [[1000, 112], [1092, 168], [1184, 108], [1060, 262], [1168, 300], [962, 214], [1236, 214]];
  const links = [[0, 1], [1, 2], [1, 3], [3, 4], [0, 5], [5, 3], [2, 6], [6, 4]];
  return (
    <svg className="lp-art" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <defs>
        <radialGradient id="glow" cx="0.42" cy="0.5" r="0.6">
          <stop offset="0" stopColor="#0f6ec8" stopOpacity="0.5" /><stop offset="1" stopColor="#22282c" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="fade" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0.55" stopColor="#22282c" stopOpacity="0" /><stop offset="1" stopColor="#22282c" stopOpacity="0.85" />
        </linearGradient>
      </defs>
      <rect width={W} height={H} fill="#22282c" />
      <rect width={W} height={H} fill="url(#glow)" />
      {poles.map((x) => (
        <g key={x} stroke="#b8c1cb" strokeOpacity="0.3" strokeWidth="2">
          <line x1={x} x2={x} y1={58} y2={350} /><line x1={x - 30} x2={x + 30} y1={74} y2={74} /><line x1={x - 20} x2={x + 20} y1={96} y2={96} />
        </g>
      ))}
      <path d={span(74, 18)} fill="none" stroke="#b8c1cb" strokeOpacity="0.35" strokeWidth="1.5" />
      <path d={span(96, 14)} fill="none" stroke="#b8c1cb" strokeOpacity="0.25" strokeWidth="1.5" />
      <path d={wave(0)} fill="none" stroke="#6fb3e8" strokeWidth="3" strokeOpacity="0.9" />
      <path d={wave((2 * Math.PI) / 3)} fill="none" stroke="#97b5c3" strokeWidth="3" strokeOpacity="0.7" />
      <path d={wave((4 * Math.PI) / 3)} fill="none" stroke="#bfe0f7" strokeWidth="2.5" strokeOpacity="0.55" />
      {links.map(([a, b], i) => <line key={i} x1={nodes[a][0]} y1={nodes[a][1]} x2={nodes[b][0]} y2={nodes[b][1]} stroke="#6fb3e8" strokeOpacity="0.45" strokeWidth="2" />)}
      {nodes.map(([x, y], i) => <g key={i}><circle cx={x} cy={y} r="16" fill="#97b5c3" opacity="0.1" /><circle cx={x} cy={y} r="5" fill="#bfe0f7" /></g>)}
      <rect width={W} height={H} fill="url(#fade)" />
    </svg>
  );
}

/** `signedIn` comes from the server (the session cookie is http-only). `roles` renders only the sign-in by role (the /sign-in page). */
export function Landing({ signedIn = false, roles = false }: { signedIn?: boolean; roles?: boolean }) {
  const [cfg, setCfg] = useState<Config | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const asked = useSearchParams().get("next") ?? "";
  const next = asked.startsWith("/") && !asked.startsWith("//") ? asked : "/command";  // same-site paths only
  useEffect(() => { api<Config>("/config").then(setCfg).catch((e: Error) => setErr(e.message)); }, []);
  // Sent here by the sign-in gate (?next=/deploy ...): say what signing in opens.
  const target = next.startsWith("/deploy") ? "the Deployment planner" : next !== "/command" ? "that page" : null;

  const hl = cfg?.highlights;
  const lc = landingCopy(cfg?.copy);
  const groups: Record<string, Persona[]> = {};
  (cfg?.personas ?? []).forEach((p) => { (groups[p.group] ??= []).push(p); });
  const names = [...lc.order.filter((g) => groups[g]), ...Object.keys(groups).filter((g) => !lc.order.includes(g))];
  const go = (key: string) => { setPicked(key); start(() => choosePersona(key, next)); };

  return (
    <div className="lp">
      <header className="lp-top">
        <Link href="/" className="lp-logo"><MMLockup sub="Cloud and AI cost governance for regulated industries" /></Link>
        {roles && <Link href="/" className="lp-open">Home</Link>}
      </header>

      {!roles && <>
      <section className="lp-hero">
        <div className="lp-wrap">
          <h1>Every cloud and AI dollar,<br /><span>explained and governed.</span></h1>
          <p className="lp-lead">MM CostIQ is our proprietary FinOps &amp; AI cost governance engine that answers to the CFO, Technology, Regulators and the Board.
            It brings bills from every cloud provider, AI model vendor and Copilot-style tool into one view built on FOCUS 1.0, the
            FinOps Foundation&apos;s open billing standard. Finance, technology, compliance and audit teams then work from the same numbers.
            CostIQ runs inside your own environment, so sensitive billing and operational data never leaves your control.</p>
        </div>
      </section>

      <section className="lp-band">
        {lc.art === "grid" ? <GridArt /> : lc.art ? <HeroArt /> : null}
        {lc.band && (
          <div className="lp-band-text">
            <div>{lc.band}</div>
            <div className="hl">cloud and AI costs, governed</div>
            <span className="bar" />
          </div>
        )}
      </section>

      {hl && (
        <section className="lp-stats">
          <div className="lp-wrap lp-stats-grid">
            <div className="st"><div><b>{fmtMoney(hl.run_rate)}</b><span>Annualized cloud &amp; AI<br />run-rate</span></div><MiniBars values={hl.spend_series} color="#6fb3e8" /></div>
            <div className="st"><div><b>{hl.forecast_variance_pct > 0 ? "+" : ""}{hl.forecast_variance_pct}%</b><span>Forecast vs<br />budget</span></div><MiniArea values={hl.variance_series} color="#0f6ec8" /></div>
            <div className="st"><div><b>{fmtMoney(hl.optimization_potential)}</b><span>Optimization<br />potential / month</span></div><MiniBars values={hl.optimization_series} color="#22c55e" /></div>
          </div>
        </section>
      )}

      <section className="lp-value">
        <div className="lp-wrap lp-value-grid">
          {lc.value.map((v) => (
            <div key={v.t}><span className="vi"><Icon name={v.icon} size={22} /></span><b>{v.t}</b><p>{v.d}</p></div>
          ))}
        </div>
      </section>
      </>}

      <section className={`lp-signin${roles ? " lp-roles" : ""}`}>
        <div className="lp-wrap">
          <div className="lp-signin-head">
            <h2>{target ? `Sign in to open ${target}` : "Sign in"}</h2>
            {signedIn
              ? <Link href={next} className="btn primary">{target ? `Open ${target}` : "Continue to CostIQ"}</Link>
              : <div className="lp-hint"><Icon name="arrow" size={16} /> Choose your role</div>}
          </div>
          {err && <div className="error" style={{ margin: "12px 0" }}>CostIQ can&apos;t reach its API: {err}</div>}
          <div className="lp-groups">
            {names.map((name) => {
              const g = lc.groups[name] ?? { tone: "tech", icon: "gear", sub: "" };
              return (
                <section key={name} className={`lp-group ${g.tone}`}>
                  <header><span className="gi"><Icon name={g.icon} size={22} /></span><div><h3>{name}</h3><p>{g.sub}</p></div></header>
                  <div className="lp-cards">
                    {groups[name].map((p) => (
                      <button key={p.key} type="button" className="lp-card" disabled={pending} onClick={() => go(p.key)}>
                        <span className="pi"><Icon name={lc.icon[p.key] ?? "target"} size={17} /></span>
                        <span className="pt"><b>{p.label}</b><span>{pending && picked === p.key ? "Signing in…" : lc.description[p.key] ?? ""}</span></span>
                        <span className="chev"><Icon name="chev" size={16} /></span>
                      </button>
                    ))}
                  </div>
                </section>
              );
            })}
          </div>
        </div>
      </section>

      <footer className="lp-foot">
        <div className="lp-wrap">
          <p className="lk"><Icon name="lock" size={14} /> Runs inside your environment{hl?.as_of ? `. Data as of ${hl.as_of}` : ""}.</p>
          <p><Link href={signedIn ? "/deploy" : "/sign-in?next=%2Fdeploy"}>Deployment planner</Link> · <Link href="/glossary">Glossary</Link></p>
          <p className="by">MM CostIQ · Miracle Minds</p>
        </div>
      </footer>
    </div>
  );
}
