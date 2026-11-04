import type { Metadata } from "next";
import { cookies } from "next/headers";
import { Suspense } from "react";
import { Landing } from "@/components/site/landing";
import "../../landing.css";

export const metadata: Metadata = { title: "CostIQ" };

// The role page: sign in by choosing a role. `?next=` (set by the sign-in gate) decides where a chosen persona lands.
async function RoleScreen() {
  const jar = await cookies();
  const signedIn = Boolean(jar.get("costiq_persona"));
  return <Landing signedIn={signedIn} roles />;
}

export default function SignInPage() {
  return <Suspense fallback={null}><RoleScreen /></Suspense>;
}
