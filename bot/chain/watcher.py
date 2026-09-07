"""Polls BSC for USDT payments to your address, and credits them.

Polling, not the event-driven WebSocket subscription ZentraShopBot's own
listener uses — see rpc.py's own docstring for why that tradeoff was made
deliberately for a starter kit. The properties that actually protect money
are unchanged: a payment is not trusted until it sits under enough
confirmed blocks to survive an ordinary chain reorganisation, and crediting
a deposit is a single atomic claim keyed on the transaction hash, so seeing
the same on-chain event twice — a restart, an overlapping poll window —
credits it at most once.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from .abi import TRANSFER_TOPIC, address_to_topic, decode_transfer, from_units
from .rpc import BscRpc, RpcError

log = logging.getLogger(__name__)

# One eth_getLogs call per this many blocks, at most. Public RPC endpoints
# commonly refuse a range wider than a few thousand blocks; capping it here
# means catching up after being offline a while costs several calls instead
# of one refused one.
MAX_BLOCK_RANGE = 2000

CHECKPOINT_KEY = "usdt_last_processed_block"

# Invoked AFTER a deposit has been credited, atomically — the balance is
# already updated by the time this runs. Its job is to tell the customer,
# never to move money.
OnCredit = Callable[[dict], Awaitable[None]]


class UsdtWatcher:
    def __init__(self, db, rpc: BscRpc, *, token_address: str, payment_address: str,
                 confirmations: int, poll_seconds: float = 15.0,
                 on_credit: OnCredit | None = None):
        self.db = db
        self.rpc = rpc
        self.token_address = token_address.lower()
        self.payment_address = payment_address.lower()
        self.confirmations = confirmations
        self.poll_seconds = poll_seconds
        self.on_credit = on_credit
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        log.info("USDT watcher started: token=%s address=%s confirmations=%d",
                 self.token_address, self.payment_address, self.confirmations)
        while not self._stop.is_set():
            try:
                await self.run_once()
            except RpcError as exc:
                log.warning("USDT watcher: RPC error, will retry: %s", exc)
            except Exception:  # noqa: BLE001
                log.exception("USDT watcher: unexpected error, will retry.")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> int:
        """One poll cycle. Returns how many payments were credited — mainly
        so a test can assert on it without scraping logs."""
        await self.db.expire_deposits()

        latest = await self.rpc.block_number()
        safe_to = latest - self.confirmations
        if safe_to < 0:
            return 0

        checkpoint_raw = await self.db.get_state(CHECKPOINT_KEY)
        # First run ever: start from the current tip rather than the token's
        # entire history — a starter has no pending deposits from before it
        # existed, and scanning from block zero would take a very long time
        # and a great many requests for nothing.
        checkpoint = int(checkpoint_raw) if checkpoint_raw is not None else safe_to - 1

        if safe_to <= checkpoint:
            return 0

        from_block = checkpoint + 1
        to_block = min(safe_to, from_block + MAX_BLOCK_RANGE - 1)

        logs = await self.rpc.get_logs(
            from_block=from_block, to_block=to_block, address=self.token_address,
            topics=[TRANSFER_TOPIC, None, address_to_topic(self.payment_address)],
        )
        decimals = await self.rpc.decimals(self.token_address)

        credited_count = 0
        for raw in logs:
            transfer = decode_transfer(raw)
            if transfer is None:
                continue
            if transfer.recipient != self.payment_address:
                continue  # the topic filter already narrows this; belt and braces
            if transfer.token != self.token_address:
                continue

            amount = from_units(transfer.value, decimals)
            deposit = await self.db.open_deposit_for_amount(amount)
            if deposit is None:
                log.warning(
                    "Received %s USDT (tx %s) matching no open deposit — a "
                    "customer may have sent an amount they were not given, "
                    "or paid after their reservation's cooldown lapsed.",
                    amount, transfer.tx_hash,
                )
                continue

            credited = await self.db.credit_deposit(deposit["id"], tx_hash=transfer.tx_hash)
            if credited is not None:
                credited_count += 1
                if self.on_credit is not None:
                    await self.on_credit(credited)

        await self.db.set_state(CHECKPOINT_KEY, str(to_block))
        return credited_count
