import postgres from "postgres";

/**
 * The same PostgreSQL the bot uses, connected as the owning role. Ported
 * from ZentraShopBot's own admin dashboard — the reasoning here is not
 * specific to that shop.
 *
 * WHICH POOLER. The received wisdom is "transaction pooler (6543) for
 * serverless". Supavisor keeps a SEPARATE pool per (tenant, mode, user,
 * database) and tears one down when it has no subscribers — the bot on the
 * VPS holds a session-mode connection open around the clock, so the
 * SESSION pool is permanently warm, while a transaction pool with nothing
 * but this dashboard using it gets torn down between visits and
 * cold-started on the next one. That cold start is what a first-visit
 * timeout after a quiet spell looks like.
 *
 * So this connects to the SESSION pooler (5432), the same one the bot
 * keeps alive. The usual objection to session mode — it holds a server
 * connection per client and exhausts the limit under load — is about a
 * public application; this is one administrator, at `max: 1` per
 * invocation with a short idle timeout.
 */

declare global {
  // eslint-disable-next-line no-var
  var __dashboardSql: ReturnType<typeof postgres> | undefined;
}

function connect() {
  const url = process.env.DATABASE_URL;
  if (!url) {
    throw new Error(
      "DATABASE_URL is not set. Use the Supabase SESSION pooler connection " +
        "string (port 5432 on ...pooler.supabase.com) — see the note above " +
        "for why that rather than the transaction pooler.",
    );
  }
  return postgres(url, {
    // ONE connection per invocation — see retry.ts's own comment on why a
    // short connect_timeout paired with retries beats one long attempt.
    max: 1,
    connect_timeout: 5,
    idle_timeout: 10,
    prepare: false,
    // Money is NUMERIC in this database and must not become a JS number.
    // 0.1 + 0.2 is a rounding error the bot went to some trouble to avoid;
    // reintroducing it in the reporting layer would make the dashboard
    // disagree with the ledger it is reporting on.
    types: {
      numeric: {
        to: 1700,
        from: [1700],
        serialize: (x: string) => x,
        parse: (x: string) => x,
      },
    },
  });
}

// Next.js reloads modules in development, and each reload would otherwise
// open another pool that nothing closes. Kept in production too: a warm
// lambda that re-evaluates this module must reuse its pool rather than
// leak a second one against the same connection limit.
export const sql = globalThis.__dashboardSql ?? connect();
globalThis.__dashboardSql = sql;

export { withRetry, MAX_DURATION } from "./retry";
