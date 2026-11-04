"use client";
// Glossary: the terms CostIQ uses, and how its forecasts, projections and estimates are calculated.
// Every formula here is the one the API applies (engines.py, prudence.py, optimization.py, board.py). The entries are the
// tenant's (copy glossary.how and glossary.terms), so each tenant reads its own accounts, filings and checks.
import { useCopy } from "@/components/copy";

type How = { t: string; eq?: string; d: string };

export function GlossaryView() {
  const { get } = useCopy();
  const how = get<How[]>("glossary.how") ?? [];
  const terms = get<[string, string][]>("glossary.terms") ?? [];
  return (
    <div className="stack gl">
      <div className="page-head"><h1>Glossary</h1></div>
      <div className="gl-toc"><a className="chip" href="#how">How figures are calculated</a><a className="chip" href="#terms">Terms</a></div>
      <section className="card" id="how">
        <h2>How figures are calculated</h2>
        <div className="how">
          {how.map((h) => (
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
        <dl>{terms.map(([t, d]) => <div key={t} style={{ display: "contents" }}><dt>{t}</dt><dd>{d}</dd></div>)}</dl>
      </section>
    </div>
  );
}
