"use client";
import type { ReactNode } from "react";
import { CountUp, Reveal } from "@/components/motion";
import { source } from "./sources";

/** Footnote marker that opens the source in a new tab; hovering shows the publisher and title. */
export function Cite({ n }: { n: number | number[] }) {
  const ns = Array.isArray(n) ? n : [n];
  return (
    <sup className="cite">
      {ns.map((k, i) => {
        const s = source(k);
        return (
          <a key={k} href={s.url} target="_blank" rel="noopener noreferrer" title={`${s.publisher}${s.date ? ` (${s.date})` : ""}: ${s.title}`}
            aria-label={`Source: ${s.publisher}, ${s.title}`}>
            {k}{i < ns.length - 1 ? "," : ""}
          </a>
        );
      })}
    </sup>
  );
}

const Out = () => (
  <svg width="9" height="9" viewBox="0 0 10 10" aria-hidden><path d="M3 1.5h5.5V7M8.5 1.5 1.5 8.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
);

function SourceLine({ ns }: { ns: number[] }) {
  return (
    <div className="h-src">
      {ns.map((k) => {
        const s = source(k);
        return (
          <a key={k} href={s.url} target="_blank" rel="noopener noreferrer" title={s.title}>
            <span className="n">{k}</span>{s.publisher}{s.date ? `, ${s.date}` : ""}<Out />
          </a>
        );
      })}
    </div>
  );
}

type Headline = { tag: string; value?: number; format?: (n: number) => string; display?: string; text: ReactNode; src: number[] };

const HEADLINES: Headline[] = [
  { tag: "Affordability", value: 18.6, format: (n) => `$${n.toFixed(1)}B`, src: [2, 3],
    text: "in rate increases requested by US utilities in the first half of 2026, against at least $1.4 trillion of capex planned through 2030." },
  { tag: "Affiliate allocation", value: 35, format: (n) => `~${Math.round(n)}`, src: [4],
    text: "allocation bases used to bill one IT function to affiliates in AEP Service Corporation's 2023 FERC Form 60, from CIS customers to transmission pole miles." },
  { tag: "Regulatory assets", value: 62, format: (n) => `${Math.round(n)} mo`, src: [5, 6],
    text: "Idaho let Idaho Power defer non-capitalized cloud fees as a regulatory asset, amortized over the contract or 62 months. Illinois rejected an 80% deferral rule 3–2 the same year." },
  { tag: "AI spend", value: 98, format: (n) => `${Math.round(n)}%`, src: [7],
    text: "of FinOps teams now manage AI spend, and AI cost management is the top skill they want to add." },
  { tag: "FERC accounts", display: "Jan 1, 2025", src: [8],
    text: <>FERC Order 898 took effect, adding dedicated computer hardware and software accounts such as 397.1 and 397.2 &ldquo;to eliminate ambiguity and improve consistency.&rdquo;</> },
  { tag: "NERC CIP", display: "Jul 1, 2028", src: [15, 16],
    text: "FERC Order 919's virtualization standards, including CIP-013-3 for supply chain, become enforceable. Infrastructure shared with BES Cyber Systems comes into CIP scope." },
  { tag: "AI programs", value: 1000, format: (n) => Math.round(n).toLocaleString("en-US"), src: [10],
    text: "Microsoft 365 Copilot licenses at UK Power Networks, up from 300. 96% adoption is reported, and Microsoft claims a 480% ROI that can't be checked against its inputs." },
];

export function Headlines() {
  return (
    <section id="headlines" className="s-heads">
      <div className="s-wrap">
        <Reveal><div className="s-kicker">Why now</div></Reveal>
        <Reveal delay={0.06}><h2 className="s-h2">The headlines behind<br /><span className="dim">the three numbers.</span></h2></Reveal>
        <Reveal delay={0.12}>
          <p className="s-heads-lead">Utilities are moving technology spend from capital to operating expense, in front of commissions asking harder
            questions about it. Every figure below comes from a public filing or report, linked at its source.</p>
        </Reveal>

        <div className="s-heads-grid">
          <Reveal className="h-card feature">
            <div className="h-tag">Rate cases</div>
            <CountUp className="h-num" value={290.1} format={(n) => `$${n.toFixed(1)}M`} duration={1.8} />
            <p className="h-text">of IT operating expense Southern California Edison asked the CPUC to approve for its 2025 test year. Its
              future applications will primarily be SaaS or cloud, which moves spend from capital to O&amp;M.</p>
            <blockquote>
              <p>&ldquo;…increases our O&amp;M license costs, as we are not able to capitalize subscriptions.&rdquo;</p>
              <footer>SCE testimony, Exhibit SCE-06 Vol. 01</footer>
            </blockquote>
            <SourceLine ns={[1]} />
          </Reveal>
          {HEADLINES.map((h, k) => (
            <Reveal key={h.tag} className="h-card" delay={0.05 * ((k % 3) + 1)}>
              <div className="h-tag">{h.tag}</div>
              {h.value != null && h.format
                ? <CountUp className="h-num" value={h.value} format={h.format} duration={1.4} />
                : <span className="h-num date">{h.display}</span>}
              <p className="h-text">{h.text}</p>
              <SourceLine ns={h.src} />
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
