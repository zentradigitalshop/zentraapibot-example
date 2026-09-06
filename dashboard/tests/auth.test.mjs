/**
 * Password checking and session signing — no database, no network.
 *
 * Run:  node --test tests/auth.test.mjs
 */

import assert from "node:assert/strict";
import test from "node:test";

process.env.ADMIN_PASSWORD = "correct-horse-battery-staple";

const {
  passwordMatches, issueSession, readSession,
} = await import("../src/lib/session.ts");

test("the right password matches", () => {
  assert.equal(passwordMatches("correct-horse-battery-staple"), true);
});

test("the wrong password does not", () => {
  assert.equal(passwordMatches("wrong"), false);
  assert.equal(passwordMatches(""), false);
});

test("a session issued now reads back valid", () => {
  const token = issueSession();
  const admin = readSession(token);
  assert.ok(admin);
  assert.equal(typeof admin.since, "number");
});

test("no cookie at all reads as not signed in", () => {
  assert.equal(readSession(undefined), null);
});

test("a tampered signature is refused", () => {
  const token = issueSession();
  const [body] = token.split(".");
  assert.equal(readSession(`${body}.notarealsignature`), null);
});

test("a tampered body is refused — the signature no longer matches it", () => {
  const token = issueSession();
  const [, signature] = token.split(".");
  const forgedBody = Buffer.from(JSON.stringify({
    since: 0, exp: Math.floor(Date.now() / 1000) + 999999,
  })).toString("base64url");
  assert.equal(readSession(`${forgedBody}.${signature}`), null);
});

test("malformed tokens do not throw", () => {
  for (const junk of ["", "not-a-token", "a.b.c", "..", "justtext"]) {
    assert.equal(readSession(junk), null, junk);
  }
});

test("changing the password invalidates every existing session", () => {
  const token = issueSession();
  assert.ok(readSession(token));
  process.env.ADMIN_PASSWORD = "a-different-password-entirely";
  assert.equal(readSession(token), null);
  process.env.ADMIN_PASSWORD = "correct-horse-battery-staple"; // restore
});
