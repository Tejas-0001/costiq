// Tenants served behind this web app. Each tenant has its own API (one tenant pack per backend process); the /api route
// handler forwards to the API of the tenant chosen on the sign-in page (cookie costiq_tenant).
//   COSTIQ_API_URL_<TENANT>   API base URL per tenant (Docker Compose sets them; on Vercel the service bindings do)
//   COSTIQ_API_URL            single-tenant fallback when no per-tenant URL is set
//   COSTIQ_DEFAULT_TENANT     tenant used before anyone chooses (default: the first available)
export const TENANT_COOKIE = "costiq_tenant";
export const TENANTS = [
  { key: "pharma", label: "Life sciences" },
  { key: "utility", label: "Electric utility" },
] as const;
const KEY = /^[a-z][a-z0-9_]{0,30}$/;

/** Tenants with an API configured (server only). Empty means single-tenant mode: no switch is shown. */
export function availableTenants(): string[] {
  return TENANTS.map((t) => t.key).filter((k) => Boolean(process.env[`COSTIQ_API_URL_${k.toUpperCase()}`]));
}

/** The tenant for a request: the cookie when it names an available tenant, else the default (server only). */
export function resolveTenant(cookie: string | undefined): string | null {
  const avail = availableTenants();
  if (!avail.length) return null;
  if (cookie && KEY.test(cookie) && avail.includes(cookie)) return cookie;
  const d = process.env.COSTIQ_DEFAULT_TENANT;
  return d && avail.includes(d) ? d : avail[0];
}

/** What the landing and sign-in pages need for the industry button: the tenants to offer and the current one (server only). */
export function tenantChoice(cookie: string | undefined): { tenants: { key: string; label: string }[]; tenant: string } {
  const avail = availableTenants();
  const tenants = avail.length > 1 ? TENANTS.filter((t) => avail.includes(t.key)).map((t) => ({ key: t.key, label: t.label })) : [];
  return { tenants, tenant: resolveTenant(cookie) ?? "" };
}

/** API base URL for a tenant (server only). */
export function apiBase(tenant: string | null): string {
  return (tenant && process.env[`COSTIQ_API_URL_${tenant.toUpperCase()}`]) || process.env.COSTIQ_API_URL || "http://127.0.0.1:8080";
}
