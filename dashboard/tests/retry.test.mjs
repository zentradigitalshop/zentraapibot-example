/**
 * withRetry() — retries a transient failure, never a real one.
 *
 * No database: these errors are hand-shaped to look like what postgres.js
 * throws, not fetched from an actual outage.
 *
 * Run:  node --test tests/retry.test.mjs
 */

import assert from "node:assert/strict";
import test from "node:test";
import { withRetry } from "../src/lib/retry.ts";

function attemptsThenSucceed(failures, code) {
  let calls = 0;
  return async () => {
    calls += 1;
    if (calls <= failures) {
      const error = new Error("connection terminated unexpectedly");
      if (code) error.code = code;
      throw error;
    }
    return calls;
  };
}

test("a transient failure is retried until it succeeds", async () => {
  const run = attemptsThenSucceed(2, "ECONNRESET");
  const result = await withRetry(run);
  assert.equal(result, 3, "should have taken three attempts total");
});

test("a Supavisor saturation message is recognised without a matching code", async () => {
  let calls = 0;
  const run = async () => {
    calls += 1;
    if (calls === 1) throw new Error("MaxClientsInSessionMode: pool is busy");
    return "ok";
  };
  assert.equal(await withRetry(run), "ok");
  assert.equal(calls, 2);
});

test("a non-transient failure is never retried", async () => {
  let calls = 0;
  const run = async () => {
    calls += 1;
    const error = new Error("duplicate key value violates unique constraint");
    error.code = "23505";
    throw error;
  };
  await assert.rejects(() => withRetry(run), /duplicate key/);
  assert.equal(calls, 1, "a real error must not be retried");
});

test("a transient failure that never clears still eventually throws", async () => {
  let calls = 0;
  const run = async () => {
    calls += 1;
    const error = new Error("connection closed");
    error.code = "CONNECTION_CLOSED";
    throw error;
  };
  await assert.rejects(() => withRetry(run), /connection closed/);
  assert.ok(calls > 1, "should have retried at least once before giving up");
});

test("success on the very first try needs no retry at all", async () => {
  let calls = 0;
  const run = async () => {
    calls += 1;
    return "immediate";
  };
  assert.equal(await withRetry(run), "immediate");
  assert.equal(calls, 1);
});
