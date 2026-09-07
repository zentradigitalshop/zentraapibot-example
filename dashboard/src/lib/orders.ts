import type { Sql } from "postgres";

type Client = Sql<Record<string, never>>;

export type OrderRow = {
  id: string;
  status: string;
  quantity: number;
  product_name: string;
  price_snapshot: string;
  zentra_price_snapshot: string | null;
  zentra_order_id: string | null;
  zentra_reference: string | null;
  error: string | null;
  created_at: string;
  completed_at: string | null;
  username: string | null;
  telegram_id: string;
};

/**
 * Orders placed through this bot's own Zentra API key, searchable by
 * anything a customer or an admin might already be holding: a username, a
 * Telegram id, or either order's own reference.
 */
export async function searchOrders(sql: Client, q = ""): Promise<OrderRow[]> {
  const term = q.trim().replace(/^@/, "");
  const digits = /^\d+$/.test(term);

  const match = term
    ? sql`AND (u.username ILIKE ${`%${term}%`}
               OR o.zentra_reference ILIKE ${`%${term}%`}
               OR o.product_name ILIKE ${`%${term}%`}
               OR (${digits} AND (u.telegram_id::text = ${term}
                                   OR o.id::text = ${term}
                                   OR o.zentra_order_id = ${term})))`
    : sql``;

  return sql<OrderRow[]>`
    SELECT o.id, o.status, o.quantity, o.product_name, o.price_snapshot,
           o.zentra_price_snapshot, o.zentra_order_id, o.zentra_reference,
           o.error, o.created_at, o.completed_at,
           u.username, u.telegram_id
      FROM orders o
      JOIN users u ON u.id = o.user_id
     WHERE TRUE ${match}
     ORDER BY o.created_at DESC
     LIMIT 200
  `;
}

export type OrderTotals = {
  orders: number;
  revenue: string;
  cost: string;
  profit: string;
};

/**
 * What's shown on the Orders page's own cards — describes what a search
 * MATCHED, not the whole table, so a filtered list and its totals never
 * disagree about what they are summarising.
 *
 * A JS reduce over Number(), not SQL SUM() — the same convention the rest
 * of this dashboard uses for a bounded, already-fetched row set (LIMIT 200
 * above): each figure is a clean decimal string with at most four decimal
 * places, and summing at most 200 of them for a DISPLAY card is not the
 * repeated-float-error case Decimal-everywhere exists to prevent. It would
 * matter here the moment this fed back into a balance or a charge — it
 * never does; it only ever draws a card.
 */
export function orderTotals(rows: OrderRow[]): OrderTotals {
  const delivered = rows.filter((r) => r.status === "delivered");
  let revenue = 0;
  let cost = 0;
  for (const row of delivered) {
    revenue += Number(row.price_snapshot);
    cost += Number(row.zentra_price_snapshot ?? 0);
  }
  return {
    orders: delivered.length,
    revenue: revenue.toFixed(4),
    cost: cost.toFixed(4),
    profit: (revenue - cost).toFixed(4),
  };
}
