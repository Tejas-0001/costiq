import type { Metadata } from "next";
import { Suspense } from "react";
import Link from "next/link";
import { cookies } from "next/headers";
import { MMLockup } from "@/components/brand";
import { PublicCopy } from "@/components/copy";
import { GlossaryView } from "@/components/views/glossary";
import { IndustryButton } from "@/components/site/industry";
import { TENANT_COOKIE, tenantChoice } from "@/lib/tenants";
import "../../landing.css";

export const metadata: Metadata = { title: "CostIQ" };

// Public: the Glossary needs no sign-in. The button says where it goes, read from the http-only session cookie. The entries
// are the tenant's, from /api/config (PublicCopy).
async function Open() {
  const signedIn = Boolean((await cookies()).get("costiq_persona"));
  return <Link href={signedIn ? "/command" : "/sign-in?next=%2Fcommand"} className="lp-open">{signedIn ? "Back to CostIQ" : "Sign in"}</Link>;
}

// The industry switch lives here (default industry: pharma).
async function Industry() {
  return <IndustryButton {...tenantChoice((await cookies()).get(TENANT_COOKIE)?.value)} />;
}

export default function GlossaryPage() {
  return (
    <div className="lp">
      <header className="lp-top">
        <Link href="/" className="lp-logo"><MMLockup sub="Cloud and AI cost governance for regulated industries" /></Link>
        <Suspense fallback={null}><Open /></Suspense>
      </header>
      <main className="lp-wrap lp-gloss"><PublicCopy><GlossaryView /></PublicCopy><Suspense fallback={null}><Industry /></Suspense></main>
    </div>
  );
}
