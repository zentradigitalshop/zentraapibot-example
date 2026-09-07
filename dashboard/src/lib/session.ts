import { createHmac, timingSafeEqual } from "node:crypto";

/**
 * One password, held in the deployment's environment. Ported from
 * ZentraShopBot's own admin dashboard — the reasoning here was already
 * worked out and tested there, and does not change for being in a starter.
 *
 * This dashboard has exactly one user. An identity provider, an accounts
 * table and a password-reset flow would all be machinery for a problem
 * that does not exist here: ADMIN_PASSWORD is the whole of authentication,
 * and changing it in your deployment's environment is the whole of user
 * management.
 *
 * The session cookie is signed with a key DERIVED from that password, which
 * gives rotation for free — change the password and every existing session
 * stops verifying immediately, on every device, without a "sign out
 * everywhere" button existing. That is the property that makes a single
 * shared secret defensible: it can be revoked in one place, in seconds.
 *
 * The cookie is HttpOnly, Secure in production and SameSite=Lax, so it is
 * not readable from JavaScript and does not ride along on cross-site
 * requests.
 *
 * THIS FILE HAS NO next/headers IMPORT ON PURPOSE — it is the pure half of
 * auth.ts, kept apart from the server action so it can be tested directly
 * with plain `node --test`, the same split already used for settings.ts
 * and credit.ts.
 */

const SESSION_COOKIE = "dashboard_admin";
const SESSION_MAX_AGE = 60 * 60 * 24 * 7; // a week

export type Admin = { since: number };

function password(): string {
  const value = process.env.ADMIN_PASSWORD;
  if (!value) {
    throw new Error(
      "ADMIN_PASSWORD is not set. Add it to your deployment's environment variables.",
    );
  }
  return value;
}

function equal(a: string, b: string): boolean {
  const left = Buffer.from(a);
  const right = Buffer.from(b);
  // timingSafeEqual throws on a length mismatch, and the mismatch itself
  // would leak the length, so compare fixed-width HMACs rather than the
  // strings. Both sides here are hex digests of the same size.
  if (left.length !== right.length) return false;
  return timingSafeEqual(left, right);
}

/** Constant-time check of a submitted password. */
export function passwordMatches(submitted: string): boolean {
  const key = "dashboard-login";
  const expected = createHmac("sha256", password()).update(key).digest("hex");
  const actual = createHmac("sha256", submitted ?? "").update(key).digest("hex");
  return equal(actual, expected);
}

// ---- the session cookie ---------------------------------------------------

function sign(payload: string): string {
  // Derived from the password, so rotating the password invalidates every
  // session issued under the old one.
  return createHmac("sha256", `session:${password()}`)
    .update(payload)
    .digest("base64url");
}

export function issueSession(): string {
  const body = JSON.stringify({
    since: Math.floor(Date.now() / 1000),
    exp: Math.floor(Date.now() / 1000) + SESSION_MAX_AGE,
  });
  const encoded = Buffer.from(body).toString("base64url");
  return `${encoded}.${sign(encoded)}`;
}

export function readSession(raw: string | undefined): Admin | null {
  if (!raw) return null;
  const [encoded, signature] = raw.split(".");
  if (!encoded || !signature) return null;

  try {
    if (!equal(signature, sign(encoded))) return null;
    const body = JSON.parse(Buffer.from(encoded, "base64url").toString());
    if (typeof body.exp !== "number" || body.exp < Date.now() / 1000) return null;
    return { since: body.since ?? 0 };
  } catch {
    // A missing ADMIN_PASSWORD reaches here. Failing closed is the only
    // safe reading of "the server cannot tell whether this session is valid".
    return null;
  }
}

export const sessionCookie = {
  name: SESSION_COOKIE,
  maxAge: SESSION_MAX_AGE,
  options: {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    path: "/",
  },
};

// ---- slowing down guessing ------------------------------------------------

/**
 * A crude per-instance attempt limiter.
 *
 * Serverless makes this weaker than it looks: each cold start is a fresh
 * process with a fresh empty map, so a determined attacker with many
 * parallel requests is not stopped by it. It is here because it costs
 * nothing and does genuinely slow the ordinary case. The real defence is a
 * long random password — see README.md, which says so rather than leaving
 * it to be inferred.
 */
const attempts = new Map<string, { count: number; until: number }>();

export function tooManyAttempts(ip: string): boolean {
  const record = attempts.get(ip);
  if (!record) return false;
  if (Date.now() > record.until) {
    attempts.delete(ip);
    return false;
  }
  return record.count >= 8;
}

export function recordFailure(ip: string): void {
  const now = Date.now();
  const record = attempts.get(ip);
  if (!record || now > record.until) {
    attempts.set(ip, { count: 1, until: now + 10 * 60 * 1000 });
    return;
  }
  record.count += 1;
}

export function clearAttempts(ip: string): void {
  attempts.delete(ip);
}
