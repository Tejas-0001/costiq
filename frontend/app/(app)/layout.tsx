import { Suspense } from "react";
import { Shell } from "@/components/shell";

// The signed-in app renders on the client after /api/me resolves the persona, so navigations into it may wait on
// that request; each page exports `instant = false` to opt out of instant-navigation validation.
export const instant = false;

// The shell reads the current path (usePathname), which is runtime data under Cache Components, so it streams in.
export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    <Suspense fallback={<div className="content muted small">Loading…</div>}>
      <Shell>{children}</Shell>
    </Suspense>
  );
}
