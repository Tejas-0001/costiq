import { RiskView } from "@/components/views/risk";

export default function RiskPage() {
  return <RiskView />;
}

// Rendered on the client once /api/me resolves the persona (see the app layout), so not validated for instant navigation.
export const instant = false;
