"use client";
// The planner's output: one deployment blueprint covering infrastructure, the client's own F5 and Cisco devices, cyber
// defense, compliance, quality gates, code custody, environments, the release route and cutover, and the development
// plan. Exports as Markdown or JSON, prints, and shares as a link. Sources are linked where each fact is used; the
// Markdown export also lists them at the end.
import { motion } from "motion/react";
import { useState } from "react";
import { EASE } from "@/components/motion";
import { Diagram, gb, Num, ReuseBar } from "./canvas";
import { FRAMEWORKS, QUESTIONS, type Answers, type Plan } from "./model";
import {
  CUSTODY, CUTOVERS, DB_STEPS, DEFENSE_SOURCES, RELEASE_SOURCES, ROUTES, STAGES, STANDARDS, TESTDATA, THREATS,
} from "./release";
import { DefenseSim, DevPlan, Lanes, Pipeline, RouteFlow, TrafficSplit } from "./release-viz";

/** "Try a risky change" plants a critical CVE; the scan stage's first gate stops it. */
const FAIL_AT = {
  stage: "scan", gate: 0,
  message: "A newly added library carries a critical vulnerability (CVSS 9.8) and a fixed version exists. Policy allows none, so the merge is blocked and a fix ticket is opened.",
};

/** The one signed build promoted through the environments, identified by digest. */
const DIGEST = "sha256:7f3c…e91a";

export const SECTIONS = [
  ["summary", "Summary"], ["hardware", "Hardware"], ["reuse", "Yours vs. provided"], ["software", "Software"], ["network", "Network, F5 and firewall"],
  ["identity", "Identity"], ["data", "Data connections"], ["defense", "Cyber defense"], ["compliance", "Compliance"], ["quality", "Quality gates"],
  ["release", "Code and release"], ["plan", "Development plan"], ["prereqs", "Prerequisites"],
] as const;

/** Primary sources the planner's rules and wording were checked against (Nov 2026). */
export const BP_SOURCES: [string, string][] = [
  ["21 CFR Part 11 (electronic records and signatures)", "https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11"],
  ["EU GMP Annex 11 (computerised systems)", "https://health.ec.europa.eu/system/files/2016-11/annex11_01-2011_en_0.pdf"],
  ["ISPE GAMP 5 Second Edition", "https://ispe.org/publications/guidance-documents/gamp-5-guide-2nd-edition"],
  ["FDA: Computer Software Assurance for Production and Quality System Software", "https://www.fda.gov/regulatory-information/search-fda-guidance-documents/computer-software-assurance-production-and-quality-system-software"],
  ["HIPAA Security Rule (45 CFR Part 164, Subpart C)", "https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-C/part-164/subpart-C"],
  ["GDPR (Regulation (EU) 2016/679)", "https://eur-lex.europa.eu/eli/reg/2016/679/oj"],
  ["NIST SP 800-53 Rev. 5 baselines (OSCAL)", "https://github.com/usnistgov/oscal-content/tree/main/nist.gov/SP800-53/rev5/json"],
  ["NIST Cybersecurity Framework 2.0", "https://nvlpubs.nist.gov/nistpubs/CSWP/NIST.CSWP.29.pdf"],
  ["CISA Cross-Sector Cybersecurity Performance Goals 2.0", "https://www.cisa.gov/sites/default/files/2025-12/CPG_Report_2.0_508c.pdf"],
  ["CISA 2026 Minimum Elements for an SBOM", "https://www.cisa.gov/resources-tools/resources/2026-minimum-elements-software-bill-materials-sbom"],
  ["NIST Cryptographic Module Validation Program (FIPS 140-3)", "https://csrc.nist.gov/projects/cryptographic-module-validation-program"],
  ["Kubernetes: ingress-nginx retirement", "https://kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/"],
  ["MinIO community repository (archived)", "https://github.com/minio/minio"],
  ["PostgreSQL versioning policy", "https://www.postgresql.org/support/versioning/"],
  ["CloudNativePG replication (synchronous, RPO 0)", "https://cloudnative-pg.io/documentation/current/replication/"],
  ["Microsoft Entra signing key rollover", "https://learn.microsoft.com/en-us/entra/identity-platform/signing-key-rollover"],
  ["Okta key rotation", "https://developer.okta.com/docs/concepts/key-rotation"],
  ["CISA data backup options (3-2-1)", "https://www.cisa.gov/sites/default/files/publications/data_backup_options.pdf"],
];

/** Where each framework's control text comes from; linked from its card. */
const FRAMEWORK_URL: Record<string, string> = {
  gxp: "https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11",
  "hipaa-gdpr": "https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-C/part-164/subpart-C",
  sox: "https://pcaobus.org/oversight/standards/auditing-standards/details/AS2201",
  "nist-csf": "https://nvlpubs.nist.gov/nistpubs/CSWP/NIST.CSWP.29.pdf",
  "nist-800-53": "https://github.com/usnistgov/oscal-content/tree/main/nist.gov/SP800-53/rev5/json",
  "iso-27001": "https://www.iso.org/standard/27001",
  "cisa-cpg": "https://www.cisa.gov/sites/default/files/2025-12/CPG_Report_2.0_508c.pdf",
  privacy: "https://oag.ca.gov/privacy/ccpa",
};

export function encodeAnswers(a: Answers): string {
  return btoa(unescape(encodeURIComponent(JSON.stringify(a)))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
export function decodeAnswers(s: string): Partial<Answers> | null {
  try { return JSON.parse(decodeURIComponent(escape(atob(s.replace(/-/g, "+").replace(/_/g, "/"))))); } catch { return null; }
}

const answerText = (a: Answers) => QUESTIONS.map((q) => {
  const v = a[q.key];
  const labels = (Array.isArray(v) ? v : [v]).map((x) => q.options.find((o) => o.value === x)?.label ?? x);
  return { q: q.ask, a: labels.length ? labels.join(", ") : "None" };
});

/** The environments the answers ask for, in promotion order. */
const lanesFor = (a: Answers) => (a.environments === "prod" ? ["prod"] : a.environments === "prod-test" ? ["qa", "prod"] : ["dev", "qa", "prod"]) as ("dev" | "qa" | "prod")[];

export function toMarkdown(p: Plan): string {
  const a = p.answers;
  const L: string[] = [];
  const t = (head: string[], rows: (string | number)[][]) => {
    L.push(`| ${head.join(" | ")} |`, `| ${head.map(() => "---").join(" | ")} |`);
    rows.forEach((r) => L.push(`| ${r.map((c) => String(c).replace(/\|/g, "/")).join(" | ")} |`));
    L.push("");
  };
  L.push("# MM CostIQ on-prem deployment blueprint", "", `${p.topology}`, "");
  L.push(`Totals: ${p.totals.vcpu} vCPU, ${p.totals.ram} GB memory, ${gb(p.totals.storage)} storage, ${p.totals.servers} new servers. About ${p.weeks} weeks to production. You already own ${p.reuse.have} of ${p.reuse.total} building blocks.`, "");
  L.push("Sizing is a starting point, confirmed in a load test on your data before production.", "");
  if (p.notes.length) { L.push("## Notes", ""); p.notes.forEach((n) => L.push(`- ${n}`)); L.push(""); }
  L.push("## Your answers", ""); t(["Question", "Answer"], answerText(a).map((x) => [x.q, x.a]));
  L.push("## Hardware and compute", "");
  p.envs.forEach((e) => { L.push(`### ${e.name}`, ""); t(["Role", "Count", "vCPU", "Memory (GB)", "Disk (GB)", "Notes"], e.servers.map((s) => [s.role, s.count || "-", s.vcpu, s.ram, s.disk, s.note ?? ""])); });
  L.push(`Database: ${p.db.engine}, ${p.db.layout}; ${p.db.vcpu} vCPU, ${p.db.ram} GB, ${p.db.storage} GB for 36 months (about ${p.db.rowsPerMonth}M rows a month).`, "");
  L.push("## What we plug into, and what we provide", "");
  L.push("**Yours (reused):**", ""); p.reuse.yours.forEach((b) => L.push(`- ${b.label}: ${b.yours}`)); if (!p.reuse.yours.length) L.push("- None selected");
  L.push("", "**Provided by Miracle Minds:**", ""); p.reuse.provided.forEach((b) => L.push(`- ${b.label}: ${b.provided}`)); L.push("");
  L.push("## Software bill of materials", ""); t(["Component", "Version", "Notes"], p.software.map((s) => [s.name, s.version, s.note]));
  L.push("## Network and firewall rules", ""); t(["From", "To", "Port", "Purpose", "Note"], p.rules.map((r) => [r.from, r.to, r.port, r.purpose, r.when ?? ""]));
  p.edgeConfig.forEach((d) => { L.push(`### ${d.product} (yours)`, "", d.role, ""); d.items.forEach((i) => L.push(`- [ ] ${i}`)); L.push(""); });
  L.push("## Identity", ""); p.identity.forEach((x) => L.push(`- ${x}`)); L.push("");
  L.push("## Data connections", ""); t(["Source", "How", "Cadence", "Owner"], p.connectors.map((c) => [c.label, c.method, c.cadence, c.owner]));
  L.push("## Cyber defense", "", `Watched by ${p.defense.soc}; ${p.defense.testing}.`, "");
  t(["Layer", "Whose", "Product", "What it does"], p.defense.layers.map((l) => [l.label, l.owner === "yours" ? "Yours" : "We provide", l.product, l.does]));
  L.push("### Attacks and where they stop", "");
  THREATS.forEach((th) => { L.push(`**${th.name}** (MITRE ATT&CK ${th.technique})`, ""); th.steps.forEach((s) => L.push(`- ${p.defense.layers.find((l) => l.id === s.layer)?.label}: ${s.text}`)); L.push(""); });
  L.push("### Detection rules", ""); t(["Alert", "Source", "Severity"], p.defense.detections.map((d) => [d.alert, d.source, d.severity]));
  L.push("## Compliance controls", "");
  p.controls.forEach((c) => { L.push(`### ${c.framework}`, ""); c.items.forEach((i) => L.push(`- ${i}`)); L.push(""); });
  if (!p.controls.length) L.push("No frameworks selected.", "");
  L.push("## Quality gates", "", "| Stage | Gate | Tool | Must be true to pass | Satisfies |", "| --- | --- | --- | --- | --- |");
  STAGES.forEach((s) => s.gates.forEach((g) => L.push(`| ${s.name} | ${g.name} | ${g.tool} | ${g.rule.replace(/\|/g, "/")} | ${g.std} |`)));
  L.push("", "### Standards", ""); STANDARDS.forEach((s) => L.push(`- [${s.name} ${s.version}](${s.url}): ${s.how}`));
  L.push("", "### Assurance pack", ""); p.assurance.forEach((x) => L.push(`- ${x}`)); L.push("");
  const cu = CUSTODY[a.custody], r = ROUTES[a.route], co = CUTOVERS[a.cutover];
  L.push("## Code custody", "", `**${cu.name}.** ${cu.short}`, ""); cu.flow.forEach((s, i) => L.push(`${i + 1}. ${s}`)); L.push("", "You receive:"); cu.get.forEach((g) => L.push(`- ${g}`));
  L.push("", "## Environments and test data", "", `${lanesFor(a).length} environment(s). Test data: **${TESTDATA[a.testdata].name}.** ${TESTDATA[a.testdata].detail}`, "");
  L.push("## Release route", "", `**${r.name}.** ${r.short}`, "", `Path: ${r.flow.map((h) => (h === "|" ? "**[your boundary]**" : h)).join(" → ")}`, "", "You need:"); r.needs.forEach((x) => L.push(`- ${x}`));
  L.push("", "Why it is safe:"); r.security.forEach((x) => L.push(`- ${x}`)); L.push("", "Trade-offs:"); r.cons.forEach((x) => L.push(`- ${x}`)); L.push("");
  t(["Route", "Fits", "Air-gapped", "Assurance (1-5)", "Release speed (1-5)", "Effort for you (1-5)"],
    Object.entries(ROUTES).map(([id, x]) => [`${x.name}${id === a.route ? " (chosen)" : ""}`, x.fit, x.airgap, x.scores.assurance, x.scores.speed, x.scores.effort]));
  L.push("## Cutover", "", `**${co.name}** via ${p.lb.via} (${co.mechanism(p.lb.f5)}). ${co.short}`, ""); co.steps.forEach((s, i) => L.push(`${i + 1}. ${s.label}: ${s.check}`));
  L.push("", `Rollback: ${co.rollback(p.lb.f5)}`, "", "Database changes:"); DB_STEPS.forEach((s, i) => L.push(`${i + 1}. ${s}`)); L.push("");
  L.push("## Development plan", "");
  p.timeline.forEach((x, i) => { L.push(`### ${i + 1}. ${x.phase} (weeks ${Math.round(x.start) + 1}-${Math.round(x.start + x.weeks)})`, "", x.detail, "", "Exit gate:"); x.exit.forEach((e) => L.push(`- [ ] ${e}`)); L.push(`- Signed by: ${x.signoff}`, ""); });
  L.push("## Team and effort", ""); t(["Role", "Side", "Days", "Does"], p.team.map((m) => [m.role, m.side, m.days, m.does]));
  L.push("## Prerequisites checklist", ""); t(["ID", "Item", "Owner", "Status", "Needed by week"], p.prerequisites.map((x) => [x.id, x.item, x.owner, x.status === "reuse" ? "Use existing" : "To do", x.week]));
  L.push("## Sources", "", "Checked against these primary sources (Nov 2026):", "");
  [...BP_SOURCES, ...RELEASE_SOURCES, ...DEFENSE_SOURCES].forEach(([s, u]) => L.push(`- [${s}](${u})`));
  return L.join("\n");
}

function download(name: string, body: string, type: string) {
  const url = URL.createObjectURL(new Blob([body], { type }));
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const rise = (i: number) => ({ initial: { opacity: 0, y: 18 }, whileInView: { opacity: 1, y: 0 }, viewport: { once: true, margin: "0px 0px -8% 0px" },
  transition: { duration: 0.55, ease: EASE, delay: Math.min(i, 6) * 0.04 } });

const Src = ({ href }: { href: string }) => <a className="dl-src" href={href} target="_blank" rel="noopener noreferrer">Source ↗</a>;
const Dots = ({ n, label }: { n: number; label: string }) => (
  <span className="dl-dots" role="img" aria-label={`${label}: ${n} of 5`}>{[1, 2, 3, 4, 5].map((i) => <i key={i} className={i <= n ? "on" : ""} />)}</span>
);

export function Blueprint({ plan, onEdit }: { plan: Plan; onEdit: () => void }) {
  const [copied, setCopied] = useState(false);
  const share = async () => {
    const url = `${location.origin}/deploy#plan=${encodeAnswers(plan.answers)}`;
    try { await navigator.clipboard.writeText(url); setCopied(true); setTimeout(() => setCopied(false), 2000); } catch { window.prompt("Copy this link", url); }
  };
  const a = plan.answers;
  const total = plan.team.reduce((n, m) => n + (m.side === "Client" ? m.days : 0), 0);
  const ours = plan.defense.layers.filter((l) => l.owner === "provided").length;
  const cu = CUSTODY[a.custody], route = ROUTES[a.route], cut = CUTOVERS[a.cutover], td = TESTDATA[a.testdata];
  return (
    <div className="dp-bp dl">
      <nav className="dp-bp-nav" aria-label="Blueprint sections">
        {SECTIONS.map(([id, label]) => <a key={id} href={`#bp-${id}`}>{label}</a>)}
        <div className="dp-bp-actions">
          <button type="button" onClick={() => download("costiq-deployment-blueprint.md", toMarkdown(plan), "text/markdown")}>Download Markdown</button>
          <button type="button" onClick={() => download("costiq-deployment-blueprint.json", JSON.stringify(plan, null, 2), "application/json")}>Download JSON</button>
          <button type="button" onClick={() => window.print()}>Print or save PDF</button>
          <button type="button" onClick={share}>{copied ? "Link copied" : "Copy share link"}</button>
          <button type="button" className="ghost" onClick={onEdit}>Change answers</button>
        </div>
      </nav>

      <div className="dp-bp-body">
        <section id="bp-summary" className="dp-sec">
          <motion.div {...rise(0)} className="dp-bp-hero">
            <span className="k">Your deployment blueprint</span>
            <h2>{plan.topology}</h2>
            <div className="dp-bp-stats">
              <div><b><Num value={plan.totals.vcpu} /></b><span>vCPU</span></div>
              <div><b><Num value={plan.totals.ram} /> GB</b><span>memory</span></div>
              <div><b>{gb(plan.totals.storage)}</b><span>storage</span></div>
              <div><b><Num value={plan.totals.servers} /></b><span>new servers</span></div>
              <div><b>≈ <Num value={plan.weeks} /></b><span>weeks to production</span></div>
            </div>
            <ReuseBar have={plan.reuse.have} total={plan.reuse.total} />
            <p className="fine">Sizing is a starting point. We confirm it with a load test on your own billing data before production.</p>
          </motion.div>
          {plan.notes.length > 0 && (
            <motion.ul {...rise(1)} className="dp-notes">{plan.notes.map((n) => <li key={n}>{n}</li>)}</motion.ul>
          )}
          <motion.div {...rise(2)} className="dp-card"><Diagram plan={plan} changed={[]} /></motion.div>
        </section>

        <section id="bp-hardware" className="dp-sec">
          <h3>Hardware and compute</h3>
          <p className="lead">{a.platform === "k8s" ? "CostIQ runs in a namespace on your cluster, so you reserve capacity instead of buying servers." : "The servers to allocate, per environment."}</p>
          {plan.envs.map((e, i) => (
            <motion.div key={e.name} {...rise(i)} className="dp-card">
              <h4>{e.name}</h4>
              <table><thead><tr><th>Role</th><th className="num">Count</th><th className="num">vCPU</th><th className="num">Memory</th><th className="num">Disk</th><th>Notes</th></tr></thead>
                <tbody>{e.servers.map((s) => (
                  <tr key={s.role}><td><b>{s.role}</b></td><td className="num">{s.count || "–"}</td><td className="num">{s.vcpu}</td><td className="num">{s.ram} GB</td><td className="num">{gb(s.disk)}</td><td className="muted">{s.note}</td></tr>
                ))}</tbody></table>
            </motion.div>
          ))}
          <motion.div {...rise(3)} className="dp-card split">
            <div><h4>Database</h4><p><b>{plan.db.engine}</b>. {plan.db.layout}.</p></div>
            <div className="kv"><span>Compute</span><b>{plan.db.vcpu} vCPU · {plan.db.ram} GB</b><span>Storage, 36 months</span><b>{gb(plan.db.storage)}</b><span>Billing rows</span><b>≈ {plan.db.rowsPerMonth}M a month</b></div>
            <div className="kv"><span>API</span><b>{plan.app.apiReplicas} replicas</b><span>Web</span><b>{plan.app.webReplicas} replicas</b><span>Ingest</span><b>{plan.app.ingest}</b></div>
          </motion.div>
        </section>

        <section id="bp-reuse" className="dp-sec">
          <h3>What we plug into, and what we provide</h3>
          <div className="dp-two">
            <motion.div {...rise(0)} className="dp-card yours"><h4>Yours, reused ({plan.reuse.yours.length})</h4>
              {plan.reuse.yours.length ? <ul>{plan.reuse.yours.map((b) => <li key={b.id}><b>{b.label}.</b> {b.yours}</li>)}</ul> : <p className="muted">Nothing selected; everything below is provided.</p>}</motion.div>
            <motion.div {...rise(1)} className="dp-card provided"><h4>We provide ({plan.reuse.provided.length})</h4>
              <ul>{plan.reuse.provided.map((b) => <li key={b.id}><b>{b.label}.</b> {b.provided}</li>)}</ul></motion.div>
          </div>
        </section>

        <section id="bp-software" className="dp-sec">
          <h3>Software bill of materials</h3>
          <motion.div {...rise(0)} className="dp-card flush"><table><thead><tr><th>Component</th><th>Version</th><th>Notes</th></tr></thead>
            <tbody>{plan.software.map((s) => <tr key={s.name + s.note}><td><b>{s.name}</b></td><td className="id">{s.version}</td><td className="muted">{s.note}</td></tr>)}</tbody></table></motion.div>
        </section>

        <section id="bp-network" className="dp-sec">
          <h3>Network, F5 and firewall</h3>
          <p className="lead">{plan.rules.length} rules. {a.have.includes("firewall") ? "They go on your existing firewall; we also apply a default-deny network policy." : "We enforce them with host firewalls and a default-deny network policy."}</p>
          <motion.div {...rise(0)} className="dp-card flush"><table><thead><tr><th>From</th><th>To</th><th>Port</th><th>Purpose</th></tr></thead>
            <tbody>{plan.rules.map((r, i) => <tr key={i}><td>{r.from}</td><td><b>{r.to}</b></td><td className="id">{r.port}</td><td className="muted">{r.purpose}{r.when && <div className="warn">{r.when}</div>}</td></tr>)}</tbody></table></motion.div>
          {plan.edgeConfig.length > 0 && (
            <>
              <h4 className="dl-sub">What your network team configures on the devices you already run</h4>
              <div className="dp-grid">
                {plan.edgeConfig.map((d, i) => (
                  <motion.div key={d.id} {...rise(i)} className="dp-card dl-device">
                    <div className="dl-device-hd"><span className="dp-badge reuse">Yours · de facto</span><Src href={d.url} /></div>
                    <h4>{d.product}</h4>
                    <p className="muted">{d.role}</p>
                    <ul className="checks done">{d.items.map((x) => <li key={x}>{x}</li>)}</ul>
                  </motion.div>
                ))}
              </div>
            </>
          )}
        </section>

        <section id="bp-identity" className="dp-sec">
          <h3>Identity and access</h3>
          <motion.ol {...rise(0)} className="dp-card dp-steps">{plan.identity.map((x) => <li key={x}>{x}</li>)}</motion.ol>
        </section>

        <section id="bp-data" className="dp-sec">
          <h3>Data connections</h3>
          {plan.connectors.length ? (
            <motion.div {...rise(0)} className="dp-card flush"><table><thead><tr><th>Source</th><th>How</th><th>Cadence</th><th>Owner</th></tr></thead>
              <tbody>{plan.connectors.map((c) => <tr key={c.label}><td><b>{c.label}</b></td><td className="muted">{c.method}</td><td>{c.cadence}</td><td>{c.owner}</td></tr>)}</tbody></table></motion.div>
          ) : <p className="muted">No sources selected.</p>}
        </section>

        <section id="bp-defense" className="dp-sec">
          <h3>Cyber defense</h3>
          <p className="lead">{plan.defense.layers.length} layers between an attacker and your cost data. {plan.defense.layers.length - ours} are yours already; we bring {ours}. Watched by {plan.defense.soc}, with {plan.defense.testing}.</p>
          <div className="dl-layers">
            {plan.defense.layers.map((l, i) => (
              <motion.div key={l.id} {...rise(i)} className={`dl-layer ${l.owner}`}>
                <span className="n">{i + 1}</span>
                <div><b>{l.label}</b><span className="prod">{l.product}</span><span className="does">{l.does}</span></div>
              </motion.div>
            ))}
          </div>
          <h4 className="dl-sub">Pick an attack and watch where it stops</h4>
          <DefenseSim layers={plan.defense.layers} />
          <div className="dp-two dl-def2">
            <motion.div {...rise(0)} className="dp-card flush">
              <table><thead><tr><th>Detection rule sent to {a.have.includes("siem") ? "your SIEM" : "Loki"}</th><th>Source</th><th>Severity</th></tr></thead>
                <tbody>{plan.defense.detections.map((d) => <tr key={d.alert}><td><b>{d.alert}</b></td><td className="muted">{d.source}</td><td><span className={`dl-sev ${d.severity.toLowerCase()}`}>{d.severity}</span></td></tr>)}</tbody></table>
            </motion.div>
            <motion.div {...rise(1)} className="dp-card">
              <div className="dl-device-hd"><h4>NIST CSF 2.0, function by function</h4><Src href="https://nvlpubs.nist.gov/nistpubs/CSWP/NIST.CSWP.29.pdf" /></div>
              <dl className="dl-csf">
                <dt>Govern</dt><dd>Supplier risk through SBOMs, signed releases, your supplier quality questionnaire and the quality agreement.</dd>
                <dt>Identify</dt><dd>This blueprint is the asset and data-flow inventory; the threat model stays current.</dd>
                <dt>Protect</dt><dd>{plan.defense.layers.filter((l) => ["firewall", "waf", "identity", "access", "pam", "supply", "segment", "secrets"].includes(l.id)).length} protective layers above.</dd>
                <dt>Detect</dt><dd>{plan.defense.detections.length} detection rules, watched by {plan.defense.soc}.</dd>
                <dt>Respond</dt><dd>A joint incident playbook; we notify your security contact and coordinate the response, as the quality agreement&apos;s incident-notice terms require.</dd>
                <dt>Recover</dt><dd>Immutable offsite backups with quarterly restore tests (CISA #StopRansomware guidance).</dd>
              </dl>
            </motion.div>
          </div>
        </section>

        <section id="bp-compliance" className="dp-sec">
          <h3>Compliance controls</h3>
          {plan.controls.length ? (
            <div className="dp-grid">{plan.controls.map((c, i) => {
              const id = FRAMEWORKS.find((f) => f.label === c.framework)?.id ?? "";
              return (
                <motion.div key={c.framework} {...rise(i)} className="dp-card"><div className="dl-device-hd"><h4>{c.framework}</h4>{FRAMEWORK_URL[id] && <Src href={FRAMEWORK_URL[id]} />}</div>
                  <ul>{c.items.map((x) => <li key={x}>{x}</li>)}</ul></motion.div>
              );
            })}</div>
          ) : <p className="muted">No frameworks selected. Common choices: {FRAMEWORKS.slice(0, 3).map((f) => f.label).join(", ")}.</p>}
        </section>

        <section id="bp-quality" className="dp-sec">
          <h3>Quality gates</h3>
          <p className="lead">The pipeline enforces the standards; nobody has to remember them. Run a change through, or try one with a known vulnerability.</p>
          <Pipeline stages={STAGES} failAt={FAIL_AT} />
          <h4 className="dl-sub">Standards we build to</h4>
          <div className="dl-stds">
            {STANDARDS.map((s, i) => (
              <motion.a key={s.id} {...rise(i)} className="dl-std" href={s.url} target="_blank" rel="noopener noreferrer">
                <span className="hd"><b>{s.name}</b><em>{s.version}</em></span>
                <span className="what">{s.what}</span>
                <span className="how">{s.how}</span>
              </motion.a>
            ))}
          </div>
          <h4 className="dl-sub">Assurance pack, delivered with every release</h4>
          <motion.ol {...rise(0)} className="dp-card dp-steps">{plan.assurance.map((x) => <li key={x}>{x}</li>)}</motion.ol>
        </section>

        <section id="bp-release" className="dp-sec">
          <h3>Code, environments and release</h3>
          <p className="lead">How the code becomes yours, what each environment may hold, and how every release reaches production.</p>
          <motion.div {...rise(0)} className="dp-card">
            <div className="dl-device-hd"><span className="dl-kicker">Code custody</span></div>
            <h4>{cu.name}</h4>
            <p className="muted">{cu.short}</p>
            <ol className="dl-steps-h">{cu.flow.map((s, i) => <li key={s}><i>{i + 1}</i>{s}</li>)}</ol>
            <div className="dl-cols3">
              <div><span className="h">You receive</span><ul>{cu.get.map((g) => <li key={g}>{g}</li>)}</ul></div>
              <div><span className="h good">Strengths</span><ul>{cu.pros.map((g) => <li key={g}>{g}</li>)}</ul></div>
              <div><span className="h warn">Watch for</span><ul>{cu.cons.map((g) => <li key={g}>{g}</li>)}</ul></div>
            </div>
          </motion.div>

          <h4 className="dl-sub">Environments: {td.name.toLowerCase()} outside production</h4>
          <Lanes envs={lanesFor(a)} digest={DIGEST} data={{ label: td.lane, tone: td.tone }} />

          <h4 className="dl-sub">Release route: {route.name}</h4>
          <motion.div {...rise(0)} className="dp-card">
            <p className="muted dl-short">{route.short}</p>
            <RouteFlow r={route} />
            <div className="dl-cols3">
              <div><span className="h">You need</span><ul>{route.needs.map((g) => <li key={g}>{g}</li>)}</ul></div>
              <div><span className="h good">Why it is safe</span><ul>{route.security.map((g) => <li key={g}>{g}</li>)}</ul></div>
              <div><span className="h warn">Trade-offs</span><ul>{route.cons.map((g) => <li key={g}>{g}</li>)}</ul></div>
            </div>
          </motion.div>
          <motion.div {...rise(1)} className="dp-card flush dl-compare">
            <table>
              <thead><tr><th>Every route we support</th><th>Fits</th><th>Air-gapped</th><th>Assurance</th><th>Release speed</th><th>Effort for you</th></tr></thead>
              <tbody>{Object.entries(ROUTES).map(([id, x]) => (
                <tr key={id} className={id === a.route ? "on" : ""}>
                  <td><b>{x.name}</b>{id === a.route && <span className="dp-badge reuse dl-chosen">Your choice</span>}</td>
                  <td className="muted">{x.fit}</td>
                  <td>{x.airgap === "yes" ? "Yes" : x.airgap === "partial" ? "With transfer" : "No"}</td>
                  <td><Dots n={x.scores.assurance} label="Assurance" /></td>
                  <td><Dots n={x.scores.speed} label="Release speed" /></td>
                  <td><Dots n={x.scores.effort} label="Effort" /></td>
                </tr>
              ))}</tbody>
            </table>
          </motion.div>

          <h4 className="dl-sub">Cutover: {cut.name.toLowerCase()} on {plan.lb.via}</h4>
          <motion.div {...rise(0)} className="dp-card">
            <p className="muted dl-short">{cut.short}</p>
            <TrafficSplit c={cut} via={plan.lb.via} f5={plan.lb.f5} />
          </motion.div>
          <motion.div {...rise(1)} className="dp-card dl-db">
            <h4>Database changes: expand, migrate, contract</h4>
            <ol className="dl-steps-h">{DB_STEPS.map((s, i) => <li key={s}><i>{i + 1}</i>{s}</li>)}</ol>
            <p className="muted">Because no release removes what the previous one needs, rolling back is a traffic change, never a database restore.</p>
          </motion.div>
        </section>

        <section id="bp-plan" className="dp-sec">
          <h3>Development plan</h3>
          <p className="lead">Gates, not dates: a phase ends when its exit criteria are met and signed. About {Math.round(total)} person-days from your teams; onboarding inside your estate is usually the critical path, not the build.</p>
          <motion.div {...rise(0)}><DevPlan timeline={plan.timeline} /></motion.div>
          <motion.div {...rise(1)} className="dp-card flush"><table><thead><tr><th>Role</th><th>Side</th><th className="num">Days</th><th>Does</th></tr></thead>
            <tbody>{plan.team.map((m) => <tr key={m.role}><td><b>{m.role}</b></td><td>{m.side}</td><td className="num">{m.days}</td><td className="muted">{m.does}</td></tr>)}</tbody></table></motion.div>
        </section>

        <section id="bp-prereqs" className="dp-sec">
          <h3>Prerequisites checklist</h3>
          <motion.div {...rise(0)} className="dp-card flush"><table><thead><tr><th>ID</th><th>Item</th><th>Owner</th><th>Status</th><th className="num">Needed by</th></tr></thead>
            <tbody>{plan.prerequisites.map((x) => (
              <tr key={x.id}><td className="id"><b>{x.id}</b></td><td>{x.item}</td><td>{x.owner}</td>
                <td><span className={`dp-badge ${x.status}`}>{x.status === "reuse" ? "Use existing" : "To do"}</span></td><td className="num">Week {x.week}</td></tr>
            ))}</tbody></table></motion.div>
        </section>
      </div>
    </div>
  );
}
