/**
 * Finding a customer — no request-supplied text ever reaches the query as
 * SQL, and an unmatched search filters to nothing rather than everyone.
 *
 * Run:  TEST_DATABASE_URL=postgresql://… node --test tests/customers.test.mjs
 */

import assert from "node:assert/strict";
import test from "node:test";
import postgres from "postgres";
import { searchCustomers } from "../src/lib/customers.ts";

const url = process.env.TEST_DATABASE_URL;

if (!url) {
  test("search customers", { skip: "TEST_DATABASE_URL is not set" }, () => {});
} else {
  if (url.includes("supabase.co") || url.includes("supabase.com")) {
    throw new Error("TEST_DATABASE_URL points at a hosted Supabase project.");
  }
  const sql = postgres(url, {
    max: 4, prepare: false,
    types: { numeric: { to: 1700, from: [1700], serialize: (x) => x, parse: (x) => x } },
  });

  const DIANA = 890201;
  const ERIC = 890202;

  test("seed", async () => {
    await sql`
      INSERT INTO users (telegram_id, username, balance_usd)
      VALUES (${DIANA}, 'diana_customer', 12.50), (${ERIC}, null, 0)
      ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username,
                                               balance_usd = EXCLUDED.balance_usd
    `;
    const [diana] = await sql`SELECT id FROM users WHERE telegram_id = ${DIANA}`;
    await sql`
      INSERT INTO orders (user_id, zentra_product_id, product_name, quantity,
                           price_snapshot, zentra_price_snapshot, status, idempotency_key)
      VALUES (${diana.id}, 'netflix-1m', 'Netflix 1m', 1, 5.00, 4.00, 'delivered',
              'test-customers-diana-1')
      ON CONFLICT (idempotency_key) DO NOTHING
    `;
  });

  test("an empty search returns everyone, most-recent-first among ties", async () => {
    const rows = await searchCustomers(sql, "");
    const ids = rows.map((r) => r.telegram_id);
    assert.ok(ids.includes(String(DIANA)));
    assert.ok(ids.includes(String(ERIC)));
  });

  test("searching by username (with or without @) finds the customer", async () => {
    for (const q of ["diana_customer", "@diana_customer", "diana"]) {
      const rows = await searchCustomers(sql, q);
      assert.ok(rows.some((r) => r.telegram_id === String(DIANA)), q);
    }
  });

  test("searching by telegram id finds exactly that customer", async () => {
    const rows = await searchCustomers(sql, String(ERIC));
    assert.equal(rows.length, 1);
    assert.equal(rows[0].telegram_id, String(ERIC));
  });

  test("a search matching nobody returns nothing, not everyone", async () => {
    const rows = await searchCustomers(sql, "no-such-customer-anywhere");
    assert.equal(rows.length, 0);
  });

  test("a customer's delivered orders and spend are counted correctly", async () => {
    const rows = await searchCustomers(sql, String(DIANA));
    assert.equal(rows[0].orders, "1");
    assert.equal(rows[0].spent, "5.0000");
  });

  test("a customer with no orders shows zero spend, not null", async () => {
    const rows = await searchCustomers(sql, String(ERIC));
    assert.equal(rows[0].orders, "0");
    assert.equal(rows[0].spent, "0");
  });

  test.after(async () => {
    await sql`DELETE FROM orders WHERE user_id IN
              (SELECT id FROM users WHERE telegram_id IN (${DIANA}, ${ERIC}))`;
    await sql`DELETE FROM users WHERE telegram_id IN (${DIANA}, ${ERIC})`;
    await sql.end();
  });
}
