// Line icons for the app navigation (24px grid, 1.8 stroke, currentColor).
const P: Record<string, string> = {
  command: "M4 13h6V4H4v9Zm0 7h6v-4H4v4Zm10 0h6v-9h-6v9Zm0-16v4h6V4h-6Z",
  explorer: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14Zm5-2 4 4",
  cloud: "M7 18h10a4 4 0 0 0 .6-7.96A6 6 0 0 0 6.1 11 3.5 3.5 0 0 0 7 18Z",
  ai: "M12 3v3m0 12v3M3 12h3m12 0h3M7 7h10v10H7V7Zm3 3h4v4h-4v-4Z",
  alerts: "M6 16v-5a6 6 0 1 1 12 0v5l1.5 2h-15L6 16Zm4 4a2 2 0 0 0 4 0",
  optimization: "M4 20 10 14l4 4 6-8M20 10V4h-6",
  anomalies: "M3 12h4l2-6 4 12 2-6h6",
  rates: "M7 4h10l3 4-8 12L4 8l3-4Zm-3 4h16M10 4 8 8l4 12 4-12-2-4",
  budgets: "M4 20V10m6 10V4m6 16v-8m4 8H2",
  allocation: "M12 4v6m0 0-6 5m6-5 6 5M6 15v5m12-5v5M9 20h6",
  prudence: "M12 3 4 6v6c0 4.4 3.4 8.2 8 9 4.6-.8 8-4.6 8-9V6l-8-3Zm-3 9 2 2 4-4",
  regulatory: "M4 20h16M6 17V9m4 8V9m4 8V9m4 8V9M3 9l9-5 9 5",
  utilities: "M13 3 5 13h6l-1 8 8-10h-6l1-8Z",
  reconciliation: "M4 7h11m0 0-3-3m3 3-3 3M20 17H9m0 0 3-3m-3 3 3 3",
  governance: "M9 12l2 2 4-4M7 3h10l2 3v15H5V6l2-3Z",
  catalog: "M5 4h10l4 4v12H5V4Zm4 7h6m-6 4h6",
  ask: "M5 18l-1 3 4-1.5A8 8 0 1 0 5 18Zm4-6h.01M12 12h.01M15 12h.01",
  deploy: "M3 8 12 3l9 5v8l-9 5-9-5V8Zm0 0 9 5 9-5m-9 5v8",
};

export function NavIcon({ name }: { name: string }) {
  return (
    <svg className="nav-ic" width="18" height="18" viewBox="0 0 24 24" aria-hidden>
      <path d={P[name] ?? P.catalog} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
