import { PrudenceView } from "@/components/views/prudence";

export default function PrudencePage() {
  return <PrudenceView />;
}

// Rendered on the client once /api/me resolves the persona (see the app layout), so not validated for instant navigation.
export const instant = false;
