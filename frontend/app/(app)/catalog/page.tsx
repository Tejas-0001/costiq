import { CatalogView } from "@/components/views/catalog";

export default function Page() {
  return <CatalogView />;
}

// Rendered on the client once /api/me resolves the persona (see the app layout), so not validated for instant navigation.
export const instant = false;
