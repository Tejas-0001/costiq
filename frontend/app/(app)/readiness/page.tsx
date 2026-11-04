import { ReadinessView } from "@/components/views/readiness";

export default function Page() {
  return <ReadinessView />;
}

// Rendered on the client once /api/me resolves the persona (see the app layout), so not validated for instant navigation.
export const instant = false;
