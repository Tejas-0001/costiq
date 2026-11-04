"use client";
// The /deploy route (signed-in only). Closing returns to the page it was opened from; shared links (#plan=...) open straight on the blueprint.
// The tenant pack (/api/config `tenant`) picks the planner's frameworks: GxP, HIPAA and GDPR for pharma, NERC CIP for the utility.
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { asTenant, type Tenant } from "./model";
import { Planner, useSharedAnswers } from "./planner";

export function DeployPage() {
  const router = useRouter();
  const shared = useSharedAnswers();
  // undefined until /api/config answers; a failed call plans for the default tenant
  const [tenant, setTenant] = useState<Tenant | undefined>(undefined);
  useEffect(() => {
    let live = true;
    api<{ tenant?: string }>("/config").then((c) => { if (live) setTenant(asTenant(c.tenant)); }).catch(() => { if (live) setTenant("pharma"); });
    return () => { live = false; };
  }, []);
  // where to go on close: links from inside the app open /deploy?from=app; the home page link opens plain /deploy.
  // Read once on mount, before the planner rewrites the query string for each question.
  useEffect(() => {
    try {
      const from = new URLSearchParams(location.search).get("from");
      if (from) sessionStorage.setItem("costiq-deploy-return", from === "app" ? "/command" : "/");
      else if (!location.search) sessionStorage.setItem("costiq-deploy-return", "/");
    } catch { /* storage unavailable */ }
  }, []);
  const close = useCallback(() => {
    let to = "/";
    try { to = sessionStorage.getItem("costiq-deploy-return") || "/"; } catch { /* storage unavailable */ }
    router.push(to);
  }, [router]);
  return (
    <div className="site dp-page">
      {shared !== undefined && tenant !== undefined && <Planner onClose={close} initial={shared} tenant={tenant} />}
    </div>
  );
}
