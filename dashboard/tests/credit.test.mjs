/**
 * Credit by hand — the fallback every payment rail keeps forever.
 *
 * The one guard that matters here is the same one bot/db.py's
 * adjust_balance() has: a debit that would overdraw must be refused by the
 * UPDATE's own WHERE clause, not by a SELECT beforehand — an admin debiting
 * a customer at the exact moment they spend their last balance is a real
 * race, not a hypothetical one.
 *
 * Run:  TEST_DATABASE_URL=postgresql://… node --test tests/credit.test.mjs
 */

import assert from "node:assert/strict";
import test from "node:test";
import postgres from "postgres";
import { creditByHand, recentAdjustments } from "../src/lib/credit.ts";

const url = process.env.TEST_DATABASE_URL;

if (!url) {
  test("credit by hand", { skip: "TEST_DATABASE_URL is not set" }, () => {});
} else {
  if (url.includes("supabase.co") || url.includes("supabase.com")) {
    throw new Error("TEST_DATABASE_URL points at a hosted Supabase project.");
  }
  const sql = postgres(url, {
    max: 6, prepare: false,
    types: { numeric: { to: 1700, from: [1700], serialize: (x) => x, parse: (x) => x } },
  });

  const ALICE = 890101;
  const BOB = 890102;
  const CAROL = 890103;

  test("seed", async () => {
    await sql`
      INSERT INTO users (telegram_id, username, balance_usd)
      VALUES (${ALICE}, 'alice_credit', 0), (${BOB}, 'bob_credit', 5.00),
             (${CAROL}, 'carol_credit', 0)
      ON CONFLICT (telegram_id) DO UPDATE SET balance_usd = EXCLUDED.balance_usd
    `;
  });

  const balanceOf = async (telegramId) => {
    const [row] = await sql`SELECT balance_usd FROM users WHERE telegram_id = ${telegramId}`;
    return row.balance_usd;
  };

  test("a credit raises the balance and is journaled with a reason", async () => {
    const result = await creditByHand(sql, {
      telegramId: ALICE, amount: "10.00", reason: "manual bank transfer, receipt #4471",
    });
    assert.equal(result.ok, true, result.ok ? "" : result.message);
    assert.equal(result.newBalance, "10.0000");
    assert.equal(await balanceOf(ALICE), "10.0000");

    const adjustments = await recentAdjustments(sql, 5);
    const mine = adjustments.find((a) => a.telegram_id === String(ALICE));
    assert.ok(mine, "the adjustment was not recorded");
    assert.equal(mine.reason, "manual bank transfer, receipt #4471");
  });

  test("a debit that fits is allowed", async () => {
    const result = await creditByHand(sql, {
      telegramId: BOB, amount: "-2.00", reason: "refund reversal",
    });
    assert.equal(result.ok, true, result.ok ? "" : result.message);
    assert.equal(await balanceOf(BOB), "3.0000");
  });

  test("a debit that would overdraw is refused, and changes nothing", async () => {
    const before = await balanceOf(BOB);
    const result = await creditByHand(sql, {
      telegramId: BOB, amount: "-1000.00", reason: "should not go through",
    });
    assert.equal(result.ok, false);
    assert.match(result.message, /below zero/);
    assert.equal(await balanceOf(BOB), before);
  });

  test("an empty reason is refused before any balance changes", async () => {
    const before = await balanceOf(CAROL);
    const result = await creditByHand(sql, { telegramId: CAROL, amount: "5.00", reason: "   " });
    assert.equal(result.ok, false);
    assert.equal(await balanceOf(CAROL), before);
  });

  test("a zero amount is refused — it is not an adjustment", async () => {
    const result = await creditByHand(sql, { telegramId: CAROL, amount: "0", reason: "test" });
    assert.equal(result.ok, false);
  });

  test("a customer who does not exist is refused, not invented", async () => {
    const result = await creditByHand(sql, {
      telegramId: 999999999, amount: "5.00", reason: "test",
    });
    assert.equal(result.ok, false);
    assert.match(result.message, /No customer/);
  });

  // THE RACE. Ten debits at once against a balance that can only satisfy
  // one of them. A check-then-write pair of statements cannot guarantee
  // exactly one winner; only a guard living in the UPDATE's own WHERE
  // clause can.
  test("ten simultaneous debits against one balance's worth: exactly one wins", async () => {
    await sql`UPDATE users SET balance_usd = 5.00 WHERE telegram_id = ${CAROL}`;
    const results = await Promise.all(
      Array.from({ length: 10 }, () =>
        creditByHand(sql, { telegramId: CAROL, amount: "-5.00", reason: "race test" })),
    );
    const won = results.filter((r) => r.ok);
    assert.equal(won.length, 1, `expected exactly one winner, got ${won.length}`);
    assert.equal(await balanceOf(CAROL), "0.0000");
  });

  test.after(async () => {
    await sql`DELETE FROM admin_adjustments WHERE user_id IN
              (SELECT id FROM users WHERE telegram_id IN (${ALICE}, ${BOB}, ${CAROL}))`;
    await sql`DELETE FROM wallet_txns WHERE user_id IN
              (SELECT id FROM users WHERE telegram_id IN (${ALICE}, ${BOB}, ${CAROL}))`;
    await sql`DELETE FROM users WHERE telegram_id IN (${ALICE}, ${BOB}, ${CAROL})`;
    await sql.end();
  });
}
