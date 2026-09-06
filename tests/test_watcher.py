"""The USDT watcher: matching a decoded Transfer to the right deposit, and
crediting it exactly once — with a fake RPC standing in for BSC, so this
runs without any real chain access.

Run:  TEST_DATABASE_URL=postgresql://… python -m tests.test_watcher
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from tests.dbfixture import fresh_db
from bot.chain.abi import TRANSFER_TOPIC, address_to_topic, to_units
from bot.chain.watcher import UsdtWatcher

TOKEN = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
PAYMENT_ADDRESS = "0x1111111111111111111111111111111111111111"
DECIMALS = 18


def transfer_log(*, to: str, amount: Decimal, tx_hash: str, block: int,
                 log_index: int = 0, token: str = TOKEN,
                 sender: str = "0x2222222222222222222222222222222222222222") -> dict:
    units = to_units(amount, DECIMALS)
    return {
        "address": token,
        "topics": [TRANSFER_TOPIC, address_to_topic(sender), address_to_topic(to)],
        "data": hex(units),
        "transactionHash": tx_hash,
        "logIndex": hex(log_index),
        "blockNumber": hex(block),
    }


class FakeRpc:
    """Enough of BscRpc to drive the watcher: canned blocks, canned logs."""

    def __init__(self):
        self.current_block = 1000
        self.logs: list[dict] = []
        self.get_logs_calls: list[tuple[int, int]] = []

    async def block_number(self) -> int:
        return self.current_block

    async def get_logs(self, *, from_block: int, to_block: int, address, topics) -> list[dict]:
        self.get_logs_calls.append((from_block, to_block))
        return [
            log for log in self.logs
            if from_block <= int(log["blockNumber"], 16) <= to_block
        ]

    async def decimals(self, token_address: str) -> int:
        return DECIMALS


async def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    db = await fresh_db()

    async def customer(telegram_id: int) -> dict:
        return await db.ensure_user(telegram_id, f"user{telegram_id}")

    # ---- the first run: no history to scan -----------------------------------------------------

    print("\nStarting cold")

    rpc = FakeRpc()
    watcher = UsdtWatcher(db, rpc, token_address=TOKEN, payment_address=PAYMENT_ADDRESS,
                          confirmations=3)
    credited = await watcher.run_once()
    assert credited == 0
    checkpoint = await db.get_state("usdt_last_processed_block")
    # A cold start (no stored checkpoint) begins one block before the safe
    # tip, so this first call scans exactly one block — not the token's
    # entire history — and checkpoints at the tip itself.
    assert checkpoint == str(rpc.current_block - 3)
    assert rpc.get_logs_calls == [(rpc.current_block - 3, rpc.current_block - 3)]
    ok("a cold start scans one block at the current tip, not the token's whole history")

    # ---- a real payment, matched and credited -----------------------------------------------------

    print("\nA matched payment")

    alice = await customer(6001)
    dep = await db.allocate_deposit(
        user_id=alice["id"], base_amount=Decimal("5.00"),
        window_minutes=60, cooldown_minutes=1440,
    )

    rpc.current_block = 1010
    rpc.logs.append(transfer_log(
        to=PAYMENT_ADDRESS, amount=dep["amount_expected"],
        tx_hash="0xpayment1", block=1005,
    ))
    credited_count = await watcher.run_once()
    assert credited_count == 1
    ok("a Transfer matching an open deposit's exact amount is credited")

    after = await db.deposit_by_id(dep["id"])
    assert after["status"] == "credited"
    assert after["tx_hash"] == "0xpayment1"
    assert await db.balance(alice["id"]) == dep["amount_credited"]
    ok("the deposit and the balance both reflect it")

    # ---- confirmations: a payment too recent is not seen yet -----------------------------------------------------

    print("\nConfirmations")

    bob = await customer(6002)
    dep2 = await db.allocate_deposit(
        user_id=bob["id"], base_amount=Decimal("2.00"),
        window_minutes=60, cooldown_minutes=1440,
    )
    rpc.current_block = 1011  # only 1 block old — fewer than 3 confirmations
    rpc.logs.append(transfer_log(
        to=PAYMENT_ADDRESS, amount=dep2["amount_expected"],
        tx_hash="0xtooSoon", block=1010,
    ))
    credited_count = await watcher.run_once()
    assert credited_count == 0
    assert (await db.deposit_by_id(dep2["id"]))["status"] == "awaiting"
    ok("a payment fewer than the required confirmations old is not credited yet")

    rpc.current_block = 1014  # now 4 blocks old — past the 3-confirmation bar
    credited_count = await watcher.run_once()
    assert credited_count == 1
    ok("and IS credited once enough blocks have passed on top of it")

    # ---- an amount matching nothing is ignored, not lost -----------------------------------------------------

    print("\nAn unrecognised amount")

    rpc.current_block = 1020
    rpc.logs.append(transfer_log(
        to=PAYMENT_ADDRESS, amount=Decimal("123.4567"), tx_hash="0xnobody", block=1015,
    ))
    result = await watcher.run_once()
    assert result == 0
    ok("a payment matching no open deposit is skipped rather than crashing the watcher")

    # ---- the same on-chain event delivered twice: no double credit -----------------------------------------------------

    print("\nSeeing the same payment twice")

    carol = await customer(6003)
    dep3 = await db.allocate_deposit(
        user_id=carol["id"], base_amount=Decimal("1.00"),
        window_minutes=60, cooldown_minutes=1440,
    )
    rpc.current_block = 1030
    rpc.logs.append(transfer_log(
        to=PAYMENT_ADDRESS, amount=dep3["amount_expected"],
        tx_hash="0xreplay", block=1025,
    ))
    first = await watcher.run_once()
    assert first == 1
    balance_after_first = await db.balance(carol["id"])

    # The SAME log, seen again — an overlapping poll window is the ordinary
    # way this happens, not a hypothetical.
    second = await watcher.run_once()
    assert second == 0, "the same payment was credited a second time"
    assert await db.balance(carol["id"]) == balance_after_first
    ok("re-processing the same block range a second time credits nothing further")

    # ---- on_credit callback fires exactly once per real credit -----------------------------------------------------

    print("\nThe notification callback")

    notified: list[dict] = []

    async def notify(deposit):
        notified.append(deposit)

    dave = await customer(6004)
    dep4 = await db.allocate_deposit(
        user_id=dave["id"], base_amount=Decimal("7.00"),
        window_minutes=60, cooldown_minutes=1440,
    )
    rpc.current_block = 1040
    rpc.logs.append(transfer_log(
        to=PAYMENT_ADDRESS, amount=dep4["amount_expected"],
        tx_hash="0xnotify1", block=1035,
    ))
    watcher.on_credit = notify
    await watcher.run_once()
    assert len(notified) == 1
    assert notified[0]["id"] == dep4["id"]
    ok("the callback fires once, with the credited deposit, and never for a non-credit")

    await db.close()
    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
