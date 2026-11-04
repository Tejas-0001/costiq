// Sign-in gate (Next.js 16 `proxy`, formerly middleware): app pages need a demo persona; the API enforces roles itself.
// The home page (/), /sign-in and the Glossary are public; everything else, including the deployment planner, needs sign-in.
import { NextResponse, type NextRequest } from "next/server";

const PUBLIC = new Set(["/", "/glossary"]);

export function proxy(request: NextRequest) {
  if (PUBLIC.has(request.nextUrl.pathname) || request.cookies.get("costiq_persona")) return NextResponse.next();
  const url = new URL("/sign-in", request.url);
  url.searchParams.set("next", request.nextUrl.pathname);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!api|sign-in|_next/static|_next/image|favicon.ico|.*\\.svg$).*)"],
};
