import { Suspense } from "react";
import { cookies } from "next/headers";
import { Landing } from "@/components/site/landing";
import "../landing.css";

// The landing is also the sign-in page; whether someone is already signed in is read from the http-only cookie here.
async function Page() {
  const signedIn = Boolean((await cookies()).get("costiq_persona"));
  return <Landing signedIn={signedIn} />;
}

export default function HomePage() {
  return <Suspense fallback={null}><Page /></Suspense>;
}
