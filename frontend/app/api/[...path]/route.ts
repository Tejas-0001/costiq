// Backend-for-frontend: forwards /api/* to the FastAPI service and adds the demo persona from the session cookie.
// The browser never talks to FastAPI directly and never sets the persona header itself.
import { cookies } from "next/headers";
import type { NextRequest } from "next/server";

const BACKEND = process.env.COSTIQ_API_URL ?? "http://127.0.0.1:8080";
const PASS_HEADERS = ["content-type", "content-disposition", "x-costiq-sha256", "x-costiq-ledger-seq"];

async function forward(request: NextRequest, ctx: RouteContext<"/api/[...path]">) {
  const { path } = await ctx.params;
  const persona = (await cookies()).get("costiq_persona")?.value;
  const url = `${BACKEND}/api/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const headers = new Headers();
  const type = request.headers.get("content-type");
  if (type) headers.set("content-type", type);
  if (persona) headers.set("x-costiq-persona", persona);
  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer(),
      cache: "no-store",
    });
  } catch {
    return Response.json({ detail: `CostIQ API is not reachable at ${BACKEND}. Start the backend (mvp/backend/start.ps1).` },
      { status: 502 });
  }
  const out = new Headers();
  for (const h of PASS_HEADERS) {
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export const GET = forward;
export const POST = forward;
