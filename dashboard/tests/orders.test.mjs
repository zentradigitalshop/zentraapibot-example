/**
 * Orders placed through this bot's own Zentra API key: searching them, and
 * the totals shown on the page's own cards.
 *
 * Run:  TEST_DATABASE_URL=postgresql://… node --test tests/orders.test.mjs
 */

import assert from "node:assert/strict";
import test from "node:test";
import postgres from "postgres";
import { searchOrders, orderTotals } from "../src/lib/orders.ts";

// ---- orderTotals(): no database ------------------------------------------

test("totals cover only delivered orders, ignoring pending and failed ones", () => {
  const rows = [
    { status: "delivered", price_snapshot: "5.00", zentra_price_snapshot: "4.00" },
    { status: "delivered", price_snapshot: "2.40", zentra_price_snapshot: "2.00" },
    { status: "pending", price_snapshot: "9.00", zentra_price_snapshot: null },
    { status: "failed", price_snapshot: "9.00", zentra_price_snapshot: null },
  ];
  const totals = orderTotals(rows);
  assert.equal(totals.orders, 2);
  assert.equal(totals.revenue, "7.4000");
  assert.equal(totals.cost, "6.0000");
  assert.equal(totals.profit, "1.4000");
});

test("a delivered order with no cost snapshot counts as zero cost, not a crash", () => {
  const rows = [
    { status: "delivered", price_snapshot: "3.00", zentra_price_snapshot: null },
  ];
  const totals = orderTotals(rows);
  assert.equal(totals.cost, "0.0000");
  assert.equal(totals.profit, "3.0000");
});

test("no orders at all totals to zero, not NaN", () => {
  const totals = orderTotals([]);
  assert.equal(totals.orders, 0);
  assert.equal(totals.revenue, "0.0000");
  assert.equal(totals.profit, "0.0000");
});

// ---- searchOrders(): a real database --------------------------------------

const url = process.env.TEST_DATABASE_URL;

if (!url) {
  test("search orders", { skip: "TEST_DATABASE_URL is not set" }, () => {});
} else {
  if (url.includes("supabase.co") || url.includes("supabase.com")) {
    throw new Error("TEST_DATABASE_URL points at a hosted Supabase project.");
  }
  const sql = postgres(url, {
    max: 4, prepare: false,
    types: { numeric: { to: 1700, from: [1700], serialize: (x) => x, parse: (x) => x } },
  });

  const FRANK = 890301;
  let frankId;

  test("seed", async () => {
    await sql`
      INSERT INTO users (telegram_id, username) VALUES (${FRANK}, 'frank_orders')
      ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username
    `;
    [{ id: frankId }] = await sql`SELECT id FROM users WHERE telegram_id = ${FRANK}`;
    await sql`
      INSERT INTO orders (user_id, zentra_product_id, product_name, quantity,
                           price_snapshot, zentra_price_snapshot, status,
                           zentra_order_id, zentra_reference, idempotency_key)
      VALUES (${frankId}, 'spotify-1m', 'Spotify 1m', 1, 3.60, 3.00, 'delivered',
              'ord_123', 'ZEN-ABC12345', 'test-orders-frank-1')
      ON CONFLICT (idempotency_key) DO NOTHING
    `;
  });

  test("searching by username finds the order", async () => {
    const rows = await searchOrders(sql, "frank_orders");
    assert.ok(rows.some((r) => r.zentra_reference === "ZEN-ABC12345"));
  });

  test("searching by the Zentra reference finds it", async () => {
    const rows = await searchOrders(sql, "ZEN-ABC12345");
    assert.equal(rows.length, 1);
    assert.equal(rows[0].product_name, "Spotify 1m");
  });

  test("searching by product name finds it", async () => {
    const rows = await searchOrders(sql, "Spotify");
    assert.ok(rows.some((r) => r.zentra_order_id === "ord_123"));
  });

  test("searching by telegram id finds it", async () => {
    const rows = await searchOrders(sql, String(FRANK));
    assert.ok(rows.some((r) => r.zentra_order_id === "ord_123"));
  });

  test("a search matching nothing returns nothing", async () => {
    const rows = await searchOrders(sql, "no-such-order-anywhere");
    assert.equal(rows.length, 0);
  });

  test.after(async () => {
    await sql`DELETE FROM orders WHERE user_id = ${frankId}`;
    await sql`DELETE FROM users WHERE telegram_id = ${FRANK}`;
    await sql.end();
  });
}
