import type { Sql } from "postgres";

/**
 * Credit or debit a customer's balance by hand, from the dashboard.
 *
 * THIS IS THE FALLBACK EVERY PAYMENT RAIL KEEPS FOREVER. A payment that
 * arrives by some method the bot does not watch — cash, a bank transfer
 * nobody automated, a goodwill credit for a bad delivery — must never mean
 * a customer simply loses out. This page is where that gets made right.
 *
 * THE SAME GUARD AS bot/db.py's adjust_balance(), because a debit made from
 * here races against the bot's own purchases exactly the way two purchases
 * race against each other: the check has to live in the UPDATE's own WHERE
 * clause, not a SELECT beforehand, or an admin debiting a customer at the
 * same moment they spend the last of their balance could both succeed and
 * take the balance negative.
 */

type Client = Sql<Record<string, never>>;

export type CreditResult =
  | { ok: true; newBalance: string }
  | { ok: false; message: string };

export async function creditByHand(
  sql: Client,
  { telegramId, amount, reason }: { telegramId: number; amount: string; reason: string },
): Promise<CreditResult> {
  const trimmedReason = reason.trim();
  if (!trimmedReason) {
    return { ok: false, message: "A reason is required — this is what a customer sees a "
      + "future admin reading their history, and what you will want when you are the one "
      + "asking why a balance changed." };
  }

  const parsed = Number(amount);
  if (!Number.isFinite(parsed) || parsed === 0) {
    return { ok: false, message: "Enter a non-zero amount. Negative debits a customer; "
      + "positive credits them." };
  }

  const [user] = await sql<{ id: string; balance_usd: string }[]>`
    SELECT id, balance_usd FROM users WHERE telegram_id = ${telegramId}
  `;
  if (!user) {
    return { ok: false, message: `No customer with Telegram id ${telegramId}.` };
  }

  return sql.begin(async (tx) => {
    const updated = await tx<{ balance_usd: string }[]>`
      UPDATE users SET balance_usd = balance_usd + ${amount}::numeric
       WHERE id = ${user.id} AND balance_usd + ${amount}::numeric >= 0
       RETURNING balance_usd
    `;
    if (updated.length === 0) {
      return {
        ok: false,
        message: `That debit would take the balance below zero (currently `
          + `${user.balance_usd} USDT).`,
      };
    }

    const [txn] = await tx<{ id: string }[]>`
      INSERT INTO wallet_txns (user_id, amount_usd, kind, ref)
      VALUES (${user.id}, ${amount}::numeric, 'admin_credit', ${`dashboard:${trimmedReason.slice(0, 64)}`})
      RETURNING id
    `;
    await tx`
      INSERT INTO admin_adjustments (user_id, amount_usd, reason, wallet_txn_id)
      VALUES (${user.id}, ${amount}::numeric, ${trimmedReason}, ${txn.id})
    `;

    return { ok: true, newBalance: updated[0].balance_usd };
  });
}

export type Adjustment = {
  id: string;
  user_id: string;
  telegram_id: string;
  username: string | null;
  amount_usd: string;
  reason: string;
  created_at: string;
};

export async function recentAdjustments(sql: Client, limit = 50): Promise<Adjustment[]> {
  return sql<Adjustment[]>`
    SELECT a.id, a.user_id, u.telegram_id, u.username, a.amount_usd, a.reason, a.created_at
      FROM admin_adjustments a
      JOIN users u ON u.id = a.user_id
     ORDER BY a.created_at DESC
     LIMIT ${limit}
  `;
}
