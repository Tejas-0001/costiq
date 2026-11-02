import { Suspense } from "react";
import { RegulatoryView } from "@/components/views/regulatory";

export default function RegulatoryPage() {
  return (
    <Suspense fallback={<div className="muted small">Loading…</div>}>
      <RegulatoryView />
    </Suspense>
  );
}

// Rendered on the client once /api/me resolves the persona (see the app layout), so not validated for instant navigation.
export const instant = false;
