import { Suspense } from "react";
import { OptimizationView } from "@/components/views/optimization";

export default function Page() {
  return (
    <Suspense fallback={<div className="muted small">Loading…</div>}>
      <OptimizationView />
    </Suspense>
  );
}

// Rendered on the client once /api/me resolves the persona (see the app layout), so not validated for instant navigation.
export const instant = false;
