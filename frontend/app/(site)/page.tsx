import { Suspense } from "react";
import { cookies } from "next/headers";
import { CtaLink, Home } from "@/components/site/home";
import "../site.css";
import "../deploy.css";

// The page is static; only the call to action depends on the sign-in cookie, so it streams in on its own.
async function Account({ variant, signedOutLabel }: { variant: "primary" | "compact"; signedOutLabel: string }) {
  const signedIn = Boolean((await cookies()).get("costiq_persona"));
  return signedIn
    ? <CtaLink href="/sign-in?next=%2Fcommand" variant={variant}>Open CostIQ</CtaLink>
    : <CtaLink href="/sign-in" variant={variant}>{signedOutLabel}</CtaLink>;
}

function Cta({ variant, label }: { variant: "primary" | "compact"; label: string }) {
  return (
    <Suspense fallback={<CtaLink href="/sign-in" variant={variant}>{label}</CtaLink>}>
      <Account variant={variant} signedOutLabel={label} />
    </Suspense>
  );
}

export default function HomePage() {
  return <Home navCta={<Cta variant="compact" label="Sign in" />} />;
}
