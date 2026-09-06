import { NextResponse, type NextRequest } from "next/server";
import {
  clearAttempts,
  issueSession,
  passwordMatches,
  recordFailure,
  sessionCookie,
  tooManyAttempts,
} from "@/lib/auth";

export const runtime = "nodejs";

function clientIp(request: NextRequest): string {
  return (
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ??
    request.headers.get("x-real-ip") ??
    "unknown"
  );
}

export async function POST(request: NextRequest) {
  const form = await request.formData();
  const submitted = String(form.get("password") ?? "");
  const ip = clientIp(request);

  const back = (error: string) => {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = `?error=${error}`;
    return NextResponse.redirect(url, { status: 303 });
  };

  if (tooManyAttempts(ip)) return back("throttled");

  let matches = false;
  try {
    matches = passwordMatches(submitted);
  } catch {
    // ADMIN_PASSWORD is not configured. Say so plainly — this is the
    // deployment's own mistake, not an attacker's, and a generic "wrong
    // password" would send someone hunting for the wrong problem.
    return back("unconfigured");
  }

  if (!matches) {
    recordFailure(ip);
    return back("wrong");
  }

  clearAttempts(ip);

  const home = request.nextUrl.clone();
  home.pathname = "/";
  home.search = "";
  const response = NextResponse.redirect(home, { status: 303 });
  response.cookies.set(sessionCookie.name, issueSession(), {
    ...sessionCookie.options,
    maxAge: sessionCookie.maxAge,
  });
  return response;
}
