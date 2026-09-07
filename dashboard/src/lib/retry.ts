/**
 * Retry policy, and the time budget the retries need. Ported unchanged from
 * ZentraShopBot's own admin dashboard — this logic has no reseller-specific
 * assumption baked into it at all.
 *
 * Deliberately in its own module, importing nothing. It used to live beside
 * the pool, which opens a connection the moment it is imported — so a test
 * of the retry logic could not run without a database, and a page importing
 * the time budget was opening a pool to read a number.
 */

/**
 * Retry a query that failed for a reason that is not about the query.
 *
 * A serverless function talking to a pooled database over the internet gets
 * a certain rate of connections closed under it — the pooler recycles one,
 * a lambda thaws holding a socket that no longer exists, Supabase restarts.
 * Every one of those surfaced as "Something went wrong", because a single
 * transient failure anywhere in a page's Promise.all rejects the whole
 * render.
 *
 * So transient failures are retried and everything else is not. The
 * distinction matters: retrying a constraint violation or a syntax error
 * three times just makes the same wrong answer arrive later.
 */
const TRANSIENT = [
  "CONNECT_TIMEOUT",
  "CONNECTION_CLOSED",
  "CONNECTION_DESTROYED",
  "CONNECTION_ENDED",
  "ECONNRESET",
  "ECONNREFUSED",
  "ETIMEDOUT",
  "EPIPE",
  "57P01", // admin_shutdown
  "57P03", // cannot_connect_now — the pooler is starting up
  "53300", // too_many_connections
  "08006", // connection_failure
  "08003", // connection_does_not_exist
  // Supavisor's own refusals. In session mode it answers with these rather
  // than a socket error when every server connection is checked out, and
  // both clear on their own within a moment.
  "XX000", // Supavisor: "Max client connections reached"
];

// Supavisor reports saturation in the message, not always in the code.
const POOLER_BUSY =
  /max client|MaxClientsInSessionMode|no connection|pool.*(full|busy)|prepared statement/i;

function isTransient(error: unknown): boolean {
  const code = (error as { code?: string })?.code ?? "";
  if (TRANSIENT.includes(code)) return true;
  const message = (error as { message?: string })?.message ?? "";
  if (POOLER_BUSY.test(message)) return true;
  return /connection|terminated|timeout|socket|ECONN|EPIPE/i.test(message);
}

// Waits between attempts, in milliseconds. The shape is deliberate: the
// first two cover a connection the pooler recycled, which is instant to
// replace. The last two span a Supavisor cold start, which takes a second
// or two. Total worst case is ~3s of waiting plus five connect attempts,
// which fits inside a 30s function budget with room.
const BACKOFF = [150, 400, 1200, 2000];

export async function withRetry<T>(run: () => Promise<T>): Promise<T> {
  let last: unknown;
  for (let attempt = 0; attempt <= BACKOFF.length; attempt++) {
    try {
      return await run();
    } catch (error) {
      if (!isTransient(error)) throw error;
      last = error;
      if (attempt < BACKOFF.length) {
        await new Promise((r) => setTimeout(r, BACKOFF[attempt]));
      }
    }
  }
  throw last;
}

/**
 * How long a page may spend before the platform gives up on it.
 *
 * The default is short enough that one slow connection attempt exhausts it,
 * and the reader gets a 504 from the platform rather than anything this
 * application chose to say. Thirty seconds is far more than a healthy
 * request needs — it exists so the retries above have somewhere to happen.
 */
export const MAX_DURATION = 30;
