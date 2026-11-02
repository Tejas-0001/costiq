import type { Metadata } from "next";
import { cookies } from "next/headers";
import { Suspense } from "react";
import { SignIn } from "@/components/views/sign-in";
import "../../site.css";

export const metadata: Metadata = { title: "Sign in · CostIQ" };

// The session cookie is http-only, so whether someone is already signed in is read here and handed to the client view.
async function RoleScreen() {
  const signedIn = Boolean((await cookies()).get("costiq_persona"));
  return <SignIn signedIn={signedIn} />;
}

export default function SignInPage() {
  return (
    <Suspense fallback={<div className="auth" />}>
      <RoleScreen />
    </Suspense>
  );
}
