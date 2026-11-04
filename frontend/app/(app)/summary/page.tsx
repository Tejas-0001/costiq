import { SummaryView } from "@/components/summary";

export default function SummaryPage() {
  return <SummaryView />;
}

// Rendered on the client once /api/me resolves the persona (see the app layout), so not validated for instant navigation.
export const instant = false;
