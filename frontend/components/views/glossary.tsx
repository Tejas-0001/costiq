// Glossary: the terms CostIQ uses, and how its forecasts, projections and estimates are calculated.
// Every formula here is the one the API applies (engines.py, prudence.py, optimization.py, board.py).

const HOW: { t: string; eq?: string; d: string }[] = [
  { t: "Month forecast",
    eq: "Forecast = non-usage charges booked this month + (usage cost month to date ÷ days elapsed) × days in month",
    d: "Fixed charges (subscriptions, commitments, support) are counted once; usage is projected at its average daily rate so far. Closed months use actual cost. Computed per cost center, then added up." },
  { t: "Budget status",
    eq: "Variance = forecast − budget",
    d: "Command Center: over budget above 0%, at risk within 3% of budget. Budgets & Forecasts: warning above +2%, critical above +5%." },
  { t: "Full-year run-rate",
    eq: "Run-rate = open-month forecast × 12",
    d: "A straight-line view of the current month, not a seasonal model. Board Pack program run-rates use spend to date ÷ days in the data window × 365." },
  { t: "Forecast confidence",
    eq: "Confidence = mean(share of month elapsed, share of spend from live adapters, allocation coverage, share of closed invoice lines that tie out)",
    d: "Rises as the month progresses and as more spend comes from live feeds that reconcile to invoices. Each input is shown next to the score." },
  { t: "Forecast-year bridge (tax filings)",
    eq: "Forecast year = review-period actuals, annualized, + renewal escalators landing in the forecast year",
    d: "Built by vendor for the filing's entity and jurisdiction (PH-05). Renewals and escalators come from the vendor register (PH-06). An invoice exception is flagged because it weakens the support." },
  { t: "How CostIQ predicts",
    d: "There is no machine-learning model and no language model in any figure. Every projection is a fixed formula over billed data, so the same data always gives the same answer, and each figure shows its formula." },
  { t: "Anomaly detection",
    eq: "Sub-account spike: daily cost > 3 × its average over the prior 14 days, and > $500\nAPI key spike: daily cost > 3σ above its trailing 14-day mean, and > $25\nCopilot Studio surge: daily credits ≥ 3 × trailing 7-day average",
    d: "Rules run over every day in the data. The excess above the baseline is reported as already spent." },
  { t: "Savings estimates",
    eq: "Commitment-eligible spend × 28% (assumed Savings Plan discount)\nStandard-tier AI cost × 50% (batch discount)\nIdle seats = seat price of seats with no activity in 28 days",
    d: "Estimates are opportunities, not savings. They stay labelled as estimates until verified." },
  { t: "Verified savings",
    eq: "Verified = 30-day run-rate before the change − run-rate after (14+ days of data)",
    d: "Measured on the scope that changed. With less than 14 days of data the estimate is shown as provisional." },
  { t: "Deduction at risk",
    eq: "Review-period spend failing the reconciliation, approved-policy, GL-account or benefit tests\n+ unused share of seat purchases\n+ share of a line equal to a savings estimate left unactioned for 60+ days",
    d: "Evidence score = cost-weighted share of evidence present. Decision support for the tax team, not a prediction of any tax authority's ruling." },
  { t: "R&D credit",
    eq: "Credit = annualized review-period recurring cloud and SaaS fees × the jurisdiction's credit rate",
    d: "Claimed where the group already claims these fees; unlock where they qualify but are not yet claimed." },
  { t: "AI summary",
    d: "The AI Summary page, and the agent's briefing, are written from the figures on the pages your role can open. CostIQ ranks what matters (forecast against budget, deduction at risk, unclaimed R&D credit, open savings, GxP findings, alerts and invoice exceptions) and writes one sentence per point. Every figure links to the panel or row it comes from, and the numbered chips list the source pages. If the Claude writer is switched on, Claude words the sentences from the same figures; it cannot type a figure itself, and a draft that states one is discarded." },
  { t: "Ask CostIQ",
    d: "Your question is matched to one of the tested use cases by keyword ranking (BM25), parameters such as periods and providers are read from the text, and the use case runs in your scope. Nothing is generated; if nothing matches, CostIQ says so." },
];

const TERMS: [string, string][] = [
  ["FOCUS 1.0", "The FinOps Foundation's open billing standard. Every provider's bill is loaded into the same FOCUS columns, so AWS, Azure, Google Cloud, Snowflake and AI vendors compare line for line."],
  ["Billed cost", "What the invoice charges for a line."],
  ["Effective cost", "Cost after discounts and with commitment purchases spread over the usage they cover. Most CostIQ figures use it."],
  ["List cost / contracted cost", "Price at public rates / at your negotiated rates. List − contracted = negotiated discount."],
  ["Commitment", "Savings Plan, reservation, committed-use discount or provisioned AI throughput (PTU) paid for in advance. Unused commitment is cost with no usage behind it."],
  ["Run-rate", "Spend projected to a year from a recent period."],
  ["Cost center / business unit", "Where cost is reported, from tags, approved allocation rules or split-cost data. Each person sees only their own."],
  ["Allocation coverage", "1 − unallocated cost ÷ total cost."],
  ["Allocation policy", "An approved, effective-dated rule that splits shared cost (for example by enrolled patients or trial sites). One person submits, another approves."],
  ["Legal entity / jurisdiction", "The company in the group that bears a cost, and the tax jurisdiction it is reported in."],
  ["Recharge / transfer pricing", "Technology cost charged from the shared-services entity to the operating entities, on a documented key."],
  ["Cost class", "opex, capital, or qre (qualifying research expenditure for R&D credits)."],
  ["Review period / forecast year", "The actual period a tax filing rests on, and the year it projects to."],
  ["Examiner request", "A tax authority's information request. CostIQ drafts the answer from a tested use case; someone else approves it."],
  ["Audit file", "A ZIP of the filing's evidence. Its SHA-256 fingerprint is sealed in the decision ledger, so anyone can check it later."],
  ["Incurred exposure / already spent", "Money already spent on waste or error (spikes, idle capacity, invoice mismatches)."],
  ["Estimated saving", "A saving that has not happened yet. Never added to verified savings."],
  ["Compliance exposure", "Spend on a workload that fails a compliance check. Reported separately; never summed with savings."],
  ["Optimization lifecycle", "Identified → approved → implemented → verified, with a different person signing each step."],
  ["GxP", "Good practice rules (clinical, laboratory, manufacturing) for systems that affect product quality or patient safety."],
  ["21 CFR Part 11 / EU GMP Annex 11", "US and EU rules for electronic records and signatures in regulated systems."],
  ["Validation review", "The documented check that a GxP system, or AI used in GxP work, does what it should. Systems are re-reviewed on a schedule."],
  ["Customer-managed keys", "Encryption keys the company controls, so the provider cannot read patient data."],
  ["Quality agreement", "The contract terms a GxP vendor must sign: quality, audit rights, change notification, data integrity."],
  ["Cost per enrolled subject", "Technology operating cost ÷ patients enrolled in trials, annualized."],
  ["Separation of duties", "Whoever submits a finding, policy, optimization or examiner response cannot approve or verify it."],
  ["Decision ledger", "A hash-chained log of every approval. Changing an old entry breaks the chain, which the Governance page checks."],
  ["Use case", "A tested, named analysis with its SQL and formula. CostIQ runs 145 of them."],
  ["Board Pack status", "In CostIQ: computed from data CostIQ holds. Partial: part of the section needs a feed or an approval. Needs data: the missing input is named; nothing is estimated in its place."],
];

export function GlossaryView() {
  return (
    <div className="stack gl">
      <div className="page-head"><h1>Glossary</h1></div>
      <div className="gl-toc"><a className="chip" href="#how">How figures are calculated</a><a className="chip" href="#terms">Terms</a></div>
      <section className="card" id="how">
        <h2>How figures are calculated</h2>
        <div className="how">
          {HOW.map((h) => (
            <div key={h.t}>
              <h3>{h.t}</h3>
              {h.eq && <div className="eq">{h.eq}</div>}
              <p>{h.d}</p>
            </div>
          ))}
        </div>
      </section>
      <section className="card" id="terms">
        <h2>Terms</h2>
        <dl>{TERMS.map(([t, d]) => <div key={t} style={{ display: "contents" }}><dt>{t}</dt><dd>{d}</dd></div>)}</dl>
      </section>
    </div>
  );
}
