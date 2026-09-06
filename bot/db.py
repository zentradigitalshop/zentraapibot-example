"""Async PostgreSQL access for YOUR customers, YOUR wallet ledger, YOUR
orders. This never talks to Zentra — that is zentra_api.py's job.

The connection and the two guards below (adjust_balance, claim_order) are
carried from ZentraShopBot with the reasoning intact, because the reasoning
does not get weaker just because this is a starter kit — a race a customer
can trigger by tapping twice is a race that WILL happen in production the
first busy day this bot has.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

log = logging.getLogger(__name__)

Row = dict[str, Any]


class Db:
    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10):
        if not dsn:
            raise RuntimeError(
                "DATABASE_URL is not set. See README.md for how to create "
                "one on Supabase's free tier."
            )
        self._dsn = dsn
        # open=False so constructing Db never blocks or raises at import
        # time; connect() opens it explicitly during startup, where a
        # failure can be logged and the process can exit cleanly instead of
        # hanging on an unawaited coroutine.
        self._pool = AsyncConnectionPool(
            dsn, min_size=min_size, max_size=max_size,
            kwargs={"row_factory": dict_row}, open=False,
        )

    async def connect(self) -> None:
        await self._pool.open(wait=True, timeout=30)
        async with self._pool.connection() as conn:
            exists = await conn.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'users'"
            )
            if await exists.fetchone() is None:
                raise RuntimeError(
                    "The 'users' table does not exist. Apply the migrations "
                    "in supabase/migrations/ before starting the bot."
                )

    async def close(self) -> None:
        await self._pool.close()

    async def execute(self, sql: str, params: tuple = ()) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return cur.rowcount

    async def fetchone(self, sql: str, params: tuple = ()) -> Row | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[Row]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            return await cur.fetchall()

    async def insert(self, sql: str, params: tuple = ()) -> int:
        """An INSERT ... RETURNING id, returning just the id."""
        row = await self.fetchone(sql, params)
        return row["id"]

    # ---- customers -----------------------------------------------------------

    async def ensure_user(self, telegram_id: int, username: str | None) -> Row:
        """Fetch this customer, creating the row on their first /start.

        ON CONFLICT DO UPDATE rather than DO NOTHING: a username changes,
        and the row should say who somebody is NOW, not who they were the
        day they first opened the bot.
        """
        row = await self.fetchone(
            "INSERT INTO users (telegram_id, username) VALUES (%s, %s) "
            "ON CONFLICT (telegram_id) DO UPDATE SET username = EXCLUDED.username "
            "RETURNING *",
            (telegram_id, username),
        )
        assert row is not None
        return row

    async def user_by_telegram_id(self, telegram_id: int) -> Row | None:
        return await self.fetchone(
            "SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))

    async def adjust_balance(
        self, user_id: int, amount: Decimal, kind: str, ref: str | None = None
    ) -> bool:
        """Move a customer's balance and journal it, atomically.

        Returns False if a debit would overdraw. THE GUARD LIVES IN THE
        UPDATE'S OWN WHERE CLAUSE — `balance_usd + %s >= 0` — not in a SELECT
        beforehand. That is what makes two purchases racing on the last of a
        balance resolve to exactly one winner: the database serialises the
        two UPDATEs, and the second one's WHERE clause sees the first one's
        result. A "check the balance, then update" pair of statements cannot
        do this — both checks can pass before either update lands.
        """
        async with self._pool.connection() as conn:
            async with conn.transaction():
                cur = await conn.execute(
                    "UPDATE users SET balance_usd = balance_usd + %s "
                    "WHERE id = %s AND balance_usd + %s >= 0",
                    (amount, user_id, amount),
                )
                if cur.rowcount == 0:
                    return False
                await conn.execute(
                    "INSERT INTO wallet_txns (user_id, amount_usd, kind, ref) "
                    "VALUES (%s, %s, %s, %s)",
                    (user_id, amount, kind, ref),
                )
        return True

    async def balance(self, user_id: int) -> Decimal:
        row = await self.fetchone(
            "SELECT balance_usd FROM users WHERE id = %s", (user_id,))
        return Decimal(row["balance_usd"]) if row else Decimal(0)

    async def wallet_history(self, user_id: int, limit: int = 20) -> list[Row]:
        return await self.fetchall(
            "SELECT * FROM wallet_txns WHERE user_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (user_id, limit),
        )

    # ---- orders -----------------------------------------------------------

    async def create_pending_order(
        self, *, user_id: int, zentra_product_id: str, product_name: str,
        quantity: int, price_snapshot: Decimal, idempotency_key: str,
    ) -> Row:
        """A row that exists from the moment money moves, before Zentra is
        ever called. An order that only got written down AFTER a successful
        Zentra call would leave a debited customer with no order to show
        for it if the process died in between — exactly the failure mode
        idempotency_key's UNIQUE constraint exists to make retryable rather
        than silent.
        """
        return await self.fetchone(
            "INSERT INTO orders (user_id, zentra_product_id, product_name, "
            "quantity, price_snapshot, idempotency_key, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'pending') RETURNING *",
            (user_id, str(zentra_product_id), product_name, quantity,
             price_snapshot, idempotency_key),
        )

    async def mark_order_delivered(
        self, order_id: int, *, zentra_order_id: str, zentra_reference: str,
        delivered_payload: str | None,
    ) -> None:
        await self.execute(
            "UPDATE orders SET status = 'delivered', zentra_order_id = %s, "
            "zentra_reference = %s, delivered_payload = %s, completed_at = now() "
            "WHERE id = %s",
            (zentra_order_id, zentra_reference, delivered_payload, order_id),
        )

    async def mark_order_unresolved(self, order_id: int, *, error: str) -> None:
        await self.execute(
            "UPDATE orders SET status = 'unresolved', error = %s WHERE id = %s",
            (error, order_id),
        )

    async def mark_order_failed(self, order_id: int, *, error: str) -> None:
        await self.execute(
            "UPDATE orders SET status = 'failed', error = %s WHERE id = %s",
            (error, order_id),
        )

    async def order_by_idempotency_key(self, key: str) -> Row | None:
        return await self.fetchone(
            "SELECT * FROM orders WHERE idempotency_key = %s", (key,))

    async def user_orders(self, user_id: int, limit: int = 50) -> list[Row]:
        return await self.fetchall(
            "SELECT * FROM orders WHERE user_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (user_id, limit),
        )

    async def order_by_id(self, order_id: int) -> Row | None:
        return await self.fetchone("SELECT * FROM orders WHERE id = %s", (order_id,))

    # ---- settings -----------------------------------------------------------

    async def live_settings(self) -> list[Row]:
        return await self.fetchall("SELECT * FROM settings ORDER BY sort_order")

    async def setting_row(self, key: str) -> Row | None:
        return await self.fetchone("SELECT * FROM settings WHERE key = %s", (key,))

    async def write_setting(self, key: str, value: str, *, updated_by: int | None) -> None:
        """Change one setting and journal the change, in one transaction —
        so there is no moment where the value changed and nothing recorded
        who did it or what it used to be."""
        async with self._pool.connection() as conn:
            async with conn.transaction():
                row = await (await conn.execute(
                    "SELECT value FROM settings WHERE key = %s", (key,)
                )).fetchone()
                old_value = row["value"] if row else None
                await conn.execute(
                    "UPDATE settings SET value = %s, updated_at = now(), "
                    "updated_by = %s WHERE key = %s",
                    (value, updated_by, key),
                )
                await conn.execute(
                    "INSERT INTO settings_history (key, old_value, new_value, changed_by) "
                    "VALUES (%s, %s, %s, %s)",
                    (key, old_value, value, updated_by),
                )
