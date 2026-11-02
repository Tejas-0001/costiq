import { Suspense } from "react";
import { UseCaseFromRoute } from "@/components/views/use-case-route";

export default function Page() {
  return (
    <Suspense fallback={<div className="muted small">Loading…</div>}>
      <UseCaseFromRoute />
    </Suspense>
  );
}

// Rendered on the client once /api/me resolves the persona (see the app layout), so not validated for instant navigation.
export const instant = false;
