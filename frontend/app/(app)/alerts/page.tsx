import { AlertsView } from "@/components/views/monitors";

export default function Page() {
  return <AlertsView />;
}

// Rendered on the client once /api/me resolves the persona (see the app layout), so not validated for instant navigation.
export const instant = false;
