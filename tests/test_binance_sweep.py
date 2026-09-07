"""The Binance Pay sweeper: matching real deposits to a fake account's Pay
history, and crediting exactly once.

Run:  TEST_DATABASE_URL=postgresql://… python -m tests.test_binance_sweep
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from tests.dbfixture import fresh_db
from bot.binance_pay import BinancePaySweeper

OWN_UID = "732609210"


class FakeBinanceClient:
    """Enough of BinancePayClient to drive the sweeper."""

    def __init__(self):
        self.uid = OWN_UID
        self.rows: list[dict] = []
        self.calls = 0

    async def transactions(self, start_ms: int, end_ms: int, limit: int = 100) -> list[dict]:
        self.calls += 1
        return [r for r in self.rows if start_ms <= r["transactionTime"] <= end_ms]


def pay_row(*, txid: str, amount: str, when_ms: int) -> dict:
    return {
        "transactionId": txid, "amount": amount, "currency": "USDT",
        "transactionTime": when_ms, "orderType": "C2C",
        "receiverInfo": {"binanceId": OWN_UID},
    }


async def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    db = await fresh_db()
    client = FakeBinanceClient()
    sweeper = BinancePaySweeper(db, client, lookback_minutes=180, sweep_seconds=20)

    async def customer(telegram_id: int) -> dict:
        return await db.ensure_user(telegram_id, f"user{telegram_id}")

    def now_ms() -> int:
        return int(datetime.now(tz=timezone.utc).timestamp() * 1000)

    # ---- nothing open: no API call at all -----------------------------------------------------

    print("\nNothing outstanding")

    credited = await sweeper.run_once()
    assert credited == 0
    assert client.calls == 0
    ok("with no open Binance Pay deposits, the sweeper makes no API call at all")

    # ---- a matched payment -----------------------------------------------------

    print("\nA matched payment")

    alice = await customer(4001)
    dep = await db.allocate_deposit(
        user_id=alice["id"], base_amount=Decimal("5.00"), method="binancepay",
        window_minutes=60, cooldown_minutes=180, tail_decimals=4,
    )
    client.rows.append(pay_row(
        txid="P_ALICE1", amount=str(dep["amount_expected"]), when_ms=now_ms()))

    credited = await sweeper.run_once()
    assert credited == 1
    assert client.calls == 1
    ok("a payment matching an open deposit's exact amount is credited")

    after = await db.deposit_by_id(dep["id"])
    assert after["status"] == "credited"
    assert after["tx_hash"] == "P_ALICE1"
    assert await db.balance(alice["id"]) == dep["amount_credited"]
    ok("the deposit and the balance both reflect it")

    # ---- one API call covers every open deposit -----------------------------------------------------

    print("\nOne sweep, many deposits")

    bob = await customer(4002)
    carol = await customer(4003)
    dep_b = await db.allocate_deposit(
        user_id=bob["id"], base_amount=Decimal("10.00"), method="binancepay",
        window_minutes=60, cooldown_minutes=180,
    )
    dep_c = await db.allocate_deposit(
        user_id=carol["id"], base_amount=Decimal("20.00"), method="binancepay",
        window_minutes=60, cooldown_minutes=180,
    )
    client.rows.append(pay_row(txid="P_BOB1", amount=str(dep_b["amount_expected"]), when_ms=now_ms()))
    client.rows.append(pay_row(txid="P_CAROL1", amount=str(dep_c["amount_expected"]), when_ms=now_ms()))

    calls_before = client.calls
    credited = await sweeper.run_once()
    assert credited == 2
    assert client.calls == calls_before + 1, "one sweep should be exactly one API call"
    ok("two customers' payments are both credited from ONE Binance API call")

    # ---- the wrong amount does not match -----------------------------------------------------

    print("\nA near-miss")

    dave = await customer(4004)
    dep_d = await db.allocate_deposit(
        user_id=dave["id"], base_amount=Decimal("7.00"), method="binancepay",
        window_minutes=60, cooldown_minutes=180,
    )
    # Off by the tail's own order of magnitude — a customer who mistyped or
    # rounded the figure they were shown.
    wrong_amount = Decimal(dep_d["amount_expected"]) + Decimal("1.00")
    client.rows.append(pay_row(txid="P_DAVE1", amount=str(wrong_amount), when_ms=now_ms()))

    credited = await sweeper.run_once()
    assert credited == 0
    assert (await db.deposit_by_id(dep_d["id"]))["status"] == "awaiting"
    assert await db.balance(dave["id"]) == Decimal("0")
    ok("a payment for the wrong amount credits nothing — no partial match, no guess")

    # ---- the same transaction seen twice: no double credit -----------------------------------------------------

    print("\nSeeing the same payment twice")

    erin = await customer(4005)
    dep_e = await db.allocate_deposit(
        user_id=erin["id"], base_amount=Decimal("3.00"), method="binancepay",
        window_minutes=60, cooldown_minutes=180,
    )
    client.rows.append(pay_row(txid="P_ERIN1", amount=str(dep_e["amount_expected"]), when_ms=now_ms()))

    first = await sweeper.run_once()
    assert first == 1
    balance_after_first = await db.balance(erin["id"])

    # The exact same transaction is still in client.rows (Pay history does
    # not forget), so an overlapping sweep sees it again.
    second = await sweeper.run_once()
    assert second == 0, "the same payment was credited a second time"
    assert await db.balance(erin["id"]) == balance_after_first
    ok("re-sweeping the same window a second time credits nothing further")

    # ---- USDT and Binance Pay deposits never collide -----------------------------------------------------

    print("\nTwo rails, the same base amount")

    frank = await customer(4006)
    usdt_dep = await db.allocate_deposit(
        user_id=frank["id"], base_amount=Decimal("15.00"), method="usdt",
        window_minutes=60, cooldown_minutes=1440,
    )
    binance_dep = await db.allocate_deposit(
        user_id=frank["id"], base_amount=Decimal("15.00"), method="binancepay",
        window_minutes=60, cooldown_minutes=180,
    )
    assert usdt_dep["id"] != binance_dep["id"]
    ok("the same customer can hold an open request on both rails for the same base amount at once")

    open_usdt = await db.open_deposit_for_amount(
        Decimal(usdt_dep["amount_expected"]), method="usdt")
    open_binance = await db.open_deposit_for_amount(
        Decimal(usdt_dep["amount_expected"]), method="binancepay")
    assert open_usdt["id"] == usdt_dep["id"]
    assert open_binance is None or open_binance["id"] != usdt_dep["id"]
    ok("looking up by amount on one rail never returns the other rail's deposit")

    # ---- the notification callback -----------------------------------------------------

    print("\nThe notification callback")

    notified = []

    async def notify(deposit):
        notified.append(deposit)

    sweeper.on_credit = notify
    grace = await customer(4007)
    dep_g = await db.allocate_deposit(
        user_id=grace["id"], base_amount=Decimal("9.00"), method="binancepay",
        window_minutes=60, cooldown_minutes=180,
    )
    client.rows.append(pay_row(txid="P_GRACE1", amount=str(dep_g["amount_expected"]), when_ms=now_ms()))
    await sweeper.run_once()
    assert len(notified) == 1 and notified[0]["id"] == dep_g["id"]
    ok("the callback fires once, with the credited deposit")

    await db.close()
    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
