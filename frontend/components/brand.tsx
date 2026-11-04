import { useId } from "react";

/** Miracle Minds mark: two figures joined as an "M" (the MM CostIQ platform's logo). */
export function MMMark({ size = 34 }: { size?: number }) {
  const id = `mmg${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`; // one gradient per instance, so a hidden copy never blanks another
  return (
    <svg width={size} height={size * 0.76} viewBox="0 0 132 100" aria-label="Miracle Minds" role="img">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#3d8fe0" /><stop offset="1" stopColor="#0a4a87" />
        </linearGradient>
      </defs>
      <circle cx="18" cy="14" r="12" fill={`url(#${id})`} />
      <circle cx="114" cy="14" r="12" fill={`url(#${id})`} />
      <path d="M6 96V48c0-8 4-13 12-13 5 0 8 2 11 6l37 44 37-44c3-4 6-6 11-6 8 0 12 5 12 13v48a4 4 0 0 1-4 4h-16a4 4 0 0 1-4-4V62L79 92c-3 4-7 6-13 6s-10-2-13-6L30 62v34a4 4 0 0 1-4 4H10a4 4 0 0 1-4-4z" fill={`url(#${id})`} />
    </svg>
  );
}

/** Miracle Minds lockup: mark, "MIRACLE MINDS", divider, CostIQ. Used in the landing header. */
export function MMLockup({ sub }: { sub?: string }) {
  return (
    <span className="mm-lockup">
      <MMMark size={38} />
      <span className="mm-word">MIRACLE<br />MINDS</span>
      <span className="mm-div" />
      <span className="ciq"><span className="ciq-name">Cost<em>IQ</em></span>{sub && <span className="ciq-sub">{sub}</span>}</span>
    </span>
  );
}
