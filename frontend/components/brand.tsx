import Link from "next/link";

/** CostIQ mark: a grid signal that resolves into a check ("provable"). The tile gradient is CSS, so no SVG ids can collide. */
export function Mark({ size = 24 }: { size?: number }) {
  return (
    <span className="mark" style={{ width: size, height: size, borderRadius: size * 0.27 }} aria-hidden>
      <svg width={size} height={size} viewBox="0 0 32 32">
        <path d="M6.5 17.5h4.2l2.6-6 4 10.5 3-6.3 1.6 2.6 4-6.3" fill="none" stroke="#fff" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  );
}

export function Wordmark({ href = "/", size = 24, tone = "auto" }: { href?: string; size?: number; tone?: "auto" | "light" | "dark" }) {
  return (
    <Link href={href} className={`wordmark ${tone}`} aria-label="CostIQ home">
      <Mark size={size} />
      <span>Cost<b>IQ</b></span>
    </Link>
  );
}
