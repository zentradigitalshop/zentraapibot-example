import { cookies } from "next/headers";

/**
 * The Next.js-runtime half of session.ts: reading the signed cookie off the
 * real request. Split out so the pure signing/checking logic in session.ts
 * can be imported and tested by plain `node --test`, which cannot resolve
 * next/headers outside the Next.js build.
 */

export {
  passwordMatches, issueSession, readSession, sessionCookie,
  tooManyAttempts, recordFailure, clearAttempts,
} from "./session";
export type { Admin } from "./session";

import { type Admin, readSession, sessionCookie } from "./session";

export async function currentAdmin(): Promise<Admin | null> {
  const store = await cookies();
  return readSession(store.get(sessionCookie.name)?.value);
}

export async function requireAdmin(): Promise<Admin> {
  const admin = await currentAdmin();
  if (!admin) throw new Error("Not signed in.");
  return admin;
}
