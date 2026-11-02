"use client";
// The /deploy route (signed-in only). Closing returns to the page it was opened from; shared links (#plan=...) open straight on the blueprint.
import { useRouter } from "next/navigation";
import { useCallback, useEffect } from "react";
import { Planner, useSharedAnswers } from "./planner";

export function DeployPage() {
  const router = useRouter();
  const shared = useSharedAnswers();
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
      {shared !== undefined && <Planner onClose={close} initial={shared} />}
    </div>
  );
}
