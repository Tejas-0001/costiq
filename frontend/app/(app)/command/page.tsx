import { CommandView } from "@/components/views/command";

export default function CommandPage() {
  return <CommandView />;
}

// Rendered on the client once /api/me resolves the persona (see the app layout), so not validated for instant navigation.
export const instant = false;
