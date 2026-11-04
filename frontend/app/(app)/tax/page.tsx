import { Suspense } from "react";
import { TaxView } from "@/components/views/tax";

export default function TaxPage() {
  return (
    <Suspense fallback={<div className="muted small">Loading…</div>}>
      <TaxView />
    </Suspense>
  );
}

// Rendered on the client once /api/me resolves the persona (see the app layout), so not validated for instant navigation.
export const instant = false;
