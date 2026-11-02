import { UtilitiesView } from "@/components/views/modules";

export default function Page() {
  return <UtilitiesView />;
}

// Rendered on the client once /api/me resolves the persona (see the app layout), so not validated for instant navigation.
export const instant = false;
