import type { Metadata } from "next";
import { DeployPage } from "@/components/deploy/page";
import "../../site.css";
import "../../deploy.css";
import "../../release.css";

export const metadata: Metadata = { title: "CostIQ" };

export default function Page() {
  return <DeployPage />;
}
