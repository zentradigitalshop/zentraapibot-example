import type { Sql } from "postgres";

type Client = Sql<Record<string, never>>;

export type CustomerRow = {
  id: string;
  telegram_id: string;
  username: string | null;
  balance_usd: string;
  banned: boolean;
  created_at: string;
  orders: string;
  spent: string;
};

/**
 * Finding one customer among all of them.
 *
 * Kept apart from the page component, and taking its `sql`, so it can be
 * tested against a real database with no session in sight. Nothing a
 * request carries reaches the statement as SQL — the search term is a
 * parameter, never interpolated, and an unmatched term filters to nothing
 * rather than falling back to everyone.
 */
export async function searchCustomers(sql: Client, q = ""): Promise<CustomerRow[]> {
  const term = q.trim().replace(/^@/, "");
  const digits = /^\d+$/.test(term);

  const match = term
    ? sql`AND (u.username ILIKE ${`%${term}%`}
               OR (${digits} AND u.telegram_id::text = ${term}))`
    : sql``;

  return sql<CustomerRow[]>`
    SELECT u.id, u.telegram_id, u.username, u.balance_usd, u.banned, u.created_at,
           (SELECT count(*) FROM orders o WHERE o.user_id = u.id
              AND o.status = 'delivered')                                   AS orders,
           coalesce((SELECT sum(price_snapshot) FROM orders o
                      WHERE o.user_id = u.id AND o.status = 'delivered'), 0) AS spent
      FROM users u
     WHERE TRUE ${match}
     ORDER BY spent DESC, u.created_at DESC
     LIMIT 200
  `;
}
