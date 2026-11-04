"use client";
// Industry switch, kept on the Glossary page only. Changing industry signs out of the current role (roles differ by
// industry) and reloads, so every page then reads the other tenant's API.
import { useTransition } from "react";
import { chooseTenant } from "@/app/actions";

export function IndustryButton({ tenants, tenant }: { tenants: { key: string; label: string }[]; tenant: string }) {
  const [pending, start] = useTransition();
  const at = tenants.findIndex((t) => t.key === tenant);
  if (tenants.length < 2 || at < 0) return null;
  const other = tenants[(at + 1) % tenants.length];
  return (
    <p className="lp-industry">Industry: <b>{tenants[at].label}</b>
      <button type="button" disabled={pending}
        onClick={() => start(async () => { await chooseTenant(other.key); window.location.reload(); })}>Change to {other.label}</button>
    </p>
  );
}
