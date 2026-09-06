/**
 * Every top-up request, on either rail, in one searchable list.
 *
 * Run:  TEST_DATABASE_URL=postgresql://… node --test tests/deposits.test.mjs
 */

import assert from "node:assert/strict";
import test from "node:test";
import postgres from "postgres";
import { searchDeposits } from "../src/lib/deposits.ts";

const url = process.env.TEST_DATABASE_URL;

if (!url) {
  test("search deposits", { skip: "TEST_DATABASE_URL is not set" }, () => {});
} else {
  if (url.includes("supabase.co") || url.includes("supabase.com")) {
    throw new Error("TEST_DATABASE_URL points at a hosted Supabase project.");
  }
  const sql = postgres(url, {
    max: 4, prepare: false,
    types: { numeric: { to: 1700, from: [1700], serialize: (x) => x, parse: (x) => x } },
  });

  const GRACE = 890401;
  let graceId;

  test("seed", async () => {
    await sql`
      INSERT INTO users (telegram_id, username) VALUES (${GRACE}, 'grace_deposits')
      ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username
    `;
    [{ id: graceId }] = await sql`SELECT id FROM users WHERE telegram_id = ${GRACE}`;
    await sql`
      INSERT INTO deposits (user_id, amount_expected, amount_credited, status, method,
                             tx_hash, expires_at, cooldown_until, credited_at)
      VALUES
        (${graceId}, 20.0037, 20.0037, 'credited', 'usdt',
         '0xdeadbeefcafefeed0000000000000000000000000000000000000000000001',
         now() + interval '1 hour', now() + interval '1 day', now()),
        (${graceId}, 15.0042, 15.0042, 'awaiting', 'binancepay', null,
         now() + interval '1 hour', now() + interval '3 hours', null)
    `;
  });

  test("an empty search returns every deposit on both rails", async () => {
    const rows = await searchDeposits(sql, "");
    const methods = rows.filter((r) => r.telegram_id === String(GRACE)).map((r) => r.method);
    assert.ok(methods.includes("usdt"));
    assert.ok(methods.includes("binancepay"));
  });

  test("searching by tx_hash finds the USDT deposit", async () => {
    const rows = await searchDeposits(sql, "deadbeefcafefeed");
    assert.equal(rows.length, 1);
    assert.equal(rows[0].method, "usdt");
  });

  test("filtering by method narrows to just that rail", async () => {
    const rows = await searchDeposits(sql, String(GRACE), "binancepay");
    assert.equal(rows.length, 1);
    assert.equal(rows[0].status, "awaiting");
  });

  test("searching by username finds both of that customer's deposits", async () => {
    const rows = await searchDeposits(sql, "grace_deposits");
    assert.equal(rows.length, 2);
  });

  test("a search matching nothing returns nothing", async () => {
    const rows = await searchDeposits(sql, "no-such-deposit-anywhere");
    assert.equal(rows.length, 0);
  });

  test.after(async () => {
    await sql`DELETE FROM deposits WHERE user_id = ${graceId}`;
    await sql`DELETE FROM users WHERE telegram_id = ${GRACE}`;
    await sql.end();
  });
}
