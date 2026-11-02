// "Plan your on-prem deployment": opens the role screen, then the planner's own route (/deploy).
import Link from "next/link";

export function DeployLauncher() {
  return (
    <div className="dp-launch-slot">
      <Link href="/sign-in?next=%2Fdeploy" className="dp-launch">
        <span className="dp-launch-in">
          <span className="glyph" aria-hidden>
            <svg width="16" height="16" viewBox="0 0 16 16"><path d="M2 4.5 8 1.5l6 3v7l-6 3-6-3v-7Zm0 0 6 3 6-3M8 7.5v7" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" /></svg>
          </span>
          Plan your on-prem deployment
          <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden><path d="M3 1.5 6.5 5 3 8.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </span>
      </Link>
    </div>
  );
}
