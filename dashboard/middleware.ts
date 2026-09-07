import { NextResponse, type NextRequest } from "next/server";

/**
 * The gate.
 *
 * Every page checks its own session too — a middleware is a convenience, not
 * a security boundary, and a page that trusts it and forgets its own check
 * is one routing change away from being public. This exists so an
 * unauthenticated visitor gets the login screen instead of an error.
 *
 * The cookie's signature is verified in the page, not here: middleware runs
 * on the Edge runtime, where node:crypto is unavailable.
 */
export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (
    pathname.startsWith("/login") ||
    pathname.startsWith("/api/auth") ||
    pathname.startsWith("/_next") ||
    pathname === "/favicon.ico"
  ) {
    return NextResponse.next();
  }

  if (!request.cookies.get("dashboard_admin")) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = "";
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
