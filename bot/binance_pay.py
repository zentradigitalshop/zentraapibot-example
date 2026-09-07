"""Binance Pay, read from YOUR OWN account — no merchant integration.

Ported closely from ZentraShopBot's own zentra/binance.py: the signing, the
clock-offset handling, and the two-signal direction check (amount sign AND
receiver id must agree) are all carried over with the reasoning intact,
because this is the one payment rail where the underlying idea — trust
your own account's authenticated history over anything a customer tells
you — does not get simpler for being in a starter kit.

THE CENTRAL RULE, unchanged from the original: a payment is credited only
when every one of these holds — incoming, USDT, exactly the expected
amount, inside the request's lifetime, never used before. Any doubt at
all, and nothing is credited and a person is told. The failure this exists
to prevent is not "a payment took longer than expected" — it is "someone
was credited money that was not theirs", and those are not comparable
costs.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

log = logging.getLogger(__name__)

PAY_PATH = "/sapi/v1/pay/transactions"
TIME_PATH = "/api/v3/time"

CODE_TIMESTAMP = -1021
CODE_SIGNATURE = -1022
CODE_BAD_KEY = -2015
CODE_UNAUTHORIZED = -2014


class BinanceError(RuntimeError):
    """The API could not be used. Never means "this payment is invalid"."""

    def __init__(self, message: str, *, code: int | None = None,
                 retryable: bool = True, operator: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        # Whether this is the DEPLOYMENT's fault (bad credentials, clock
        # drift) rather than a passing failure — so it is the operator who
        # gets told, not a customer mid-payment.
        self.operator = operator


class BinancePayClient:
    """A signed, read-only client for one Binance account.

    The secret signs and is never logged, never returned, never placed in
    an exception message, and never sent anywhere but in the signature.
    """

    def __init__(self, uid: str, api_key: str, api_secret: str, *,
                 base_url: str = "https://api.binance.com", timeout: float = 15.0):
        self.uid = uid
        self._api_key = api_key
        self._api_secret = api_secret
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout)
        self._offset_ms: int | None = None
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    def _sign(self, params: dict) -> str:
        query = urllib.parse.urlencode(params)
        signature = hmac.new(
            self._api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        return f"{query}&signature={signature}"

    async def _timestamp(self) -> int:
        if self._offset_ms is None:
            await self.sync_clock()
        return int(time.time() * 1000) + (self._offset_ms or 0)

    async def sync_clock(self) -> int:
        """Learn how far this server's clock is from Binance's.

        A signed request outside Binance's accepted window is rejected in a
        way that reads exactly like a bad signature — so the offset is
        measured once and applied, rather than discovered painfully.
        """
        async with self._lock:
            try:
                response = await self._http.get(TIME_PATH)
                body = response.json()
                server_ms = int(body["serverTime"])
            except Exception as exc:  # noqa: BLE001
                raise self._describe(None, exc) from None

            self._offset_ms = server_ms - int(time.time() * 1000)
            if abs(self._offset_ms) > 5000:
                log.warning(
                    "This server's clock is %d ms from Binance's. Signed "
                    "requests are compensated, but the system clock should "
                    "be fixed (NTP).", self._offset_ms,
                )
            return self._offset_ms

    def _describe(self, response, exc: Exception | None = None) -> BinanceError:
        if response is None:
            return BinanceError(f"Could not reach Binance ({type(exc).__name__}).")

        status = response.status_code
        code = None
        message = ""
        try:
            body = response.json()
            code = body.get("code")
            message = str(body.get("msg") or "")
        except Exception:  # noqa: BLE001
            message = response.text[:200]

        if status == 451 or "restricted location" in message.lower():
            return BinanceError(
                "Binance refuses this server's location — the Binance Pay "
                "rail cannot work from here.", code=code, retryable=False, operator=True)
        if status in (429, 418):
            return BinanceError("Binance is rate limiting this account.",
                                code=code, retryable=True)
        if code == CODE_TIMESTAMP:
            self._offset_ms = None  # re-learn it; the next attempt may succeed
            return BinanceError(
                "This server's clock drifted out of Binance's accepted window.",
                code=code, retryable=True, operator=True)
        if code in (CODE_SIGNATURE, CODE_BAD_KEY, CODE_UNAUTHORIZED):
            return BinanceError(
                "Binance rejected the API credentials — check the key is "
                "correct, read-enabled, and permitted from this server's IP.",
                code=code, retryable=False, operator=True)
        return BinanceError(
            f"Binance returned HTTP {status}" + (f" (code {code})" if code else ""),
            code=code, retryable=status >= 500)

    async def transactions(self, start_ms: int, end_ms: int, limit: int = 100) -> list[dict]:
        """Pay history for a window. Read-only, the only endpoint used."""
        params = {
            "startTime": int(start_ms), "endTime": int(end_ms), "limit": int(limit),
            "recvWindow": 5000, "timestamp": await self._timestamp(),
        }
        try:
            response = await self._http.get(
                f"{PAY_PATH}?{self._sign(params)}",
                headers={"X-MBX-APIKEY": self._api_key},
            )
        except Exception as exc:  # noqa: BLE001
            raise self._describe(None, exc) from None

        if response.status_code != 200:
            raise self._describe(response)

        try:
            body = response.json()
        except ValueError:
            raise BinanceError("Binance returned a response that is not JSON.") from None

        if not isinstance(body, dict) or not body.get("success"):
            raise BinanceError(
                f"Binance reported failure (code {body.get('code') if isinstance(body, dict) else '?'}).")

        rows = body.get("data")
        if not isinstance(rows, list):
            # A shape we do not understand is not "no payment found" — that
            # would tell a customer who really did pay that they did not.
            raise BinanceError("Binance's response did not contain a transaction list.")
        return rows


@dataclass(frozen=True)
class Transfer:
    txid: str
    amount: Decimal
    currency: str
    when_ms: int
    incoming: bool

    @property
    def when(self) -> datetime:
        return datetime.fromtimestamp(self.when_ms / 1000, tz=timezone.utc)


def _receiver_id(row: dict) -> str | None:
    block = row.get("receiverInfo")
    if isinstance(block, dict) and block.get("binanceId") is not None:
        return str(block["binanceId"])
    return None


def parse(row: Any, own_uid: str) -> Transfer | None:
    """Read one transaction. None means "not usable", never "not a payment".

    Direction is established TWICE and both must agree: the sign of the
    amount, and whether the receiver id is this account's own. The two
    signals agreeing costs nothing when they do; the one time they might
    not — a schema change, a transaction type nobody anticipated — this
    returns None and the payment goes to a person instead of the wrong
    customer.
    """
    if not isinstance(row, dict):
        return None

    txid = row.get("transactionId")
    if not isinstance(txid, str) or not txid.strip():
        return None

    try:
        amount = Decimal(str(row["amount"]))
    except (KeyError, TypeError, InvalidOperation, ArithmeticError):
        return None

    when_ms = row.get("transactionTime")
    if not isinstance(when_ms, (int, float)) or when_ms <= 0:
        return None

    by_sign = amount > 0
    receiver = _receiver_id(row)
    by_receiver = receiver is not None and str(receiver) == str(own_uid)

    if by_sign != by_receiver:
        log.warning(
            "Binance transaction %s is ambiguous: amount %s says %s but "
            "receiver %s says %s. Ignoring it.",
            txid, amount, "incoming" if by_sign else "outgoing",
            receiver, "incoming" if by_receiver else "outgoing",
        )
        return None

    return Transfer(
        txid=txid.strip(), amount=abs(amount),
        currency=str(row.get("currency") or "").upper(),
        when_ms=int(when_ms), incoming=by_sign,
    )


def matching_transfer(rows: list, *, expected: Decimal, own_uid: str,
                      opened_ms: int, closed_ms: int) -> Transfer | None:
    """The one transaction that could settle a deposit expecting `expected`,
    or None. Deliberately strict — each condition is a way money could go
    to the wrong person if relaxed:

        incoming        an outgoing payment credited to a customer is a gift
        USDT            another asset at the same number is not the same value
        exact amount    no tolerance — an underpayment and a fee look alike,
                        and a tolerance is a discount anyone could discover
        in the window   a payment made before the request existed was not for it

    Returns None on ZERO matches as well as on MORE THAN ONE — the unique
    amount index makes two genuine matches for one figure nearly
    impossible, and "nearly" is not a basis for guessing which one is real.
    """
    found = []
    for row in rows:
        transfer = parse(row, own_uid)
        if transfer is None or not transfer.incoming:
            continue
        if transfer.currency != "USDT":
            continue
        if transfer.amount != expected:
            continue
        if not (opened_ms <= transfer.when_ms <= closed_ms):
            continue
        found.append(transfer)

    if len(found) != 1:
        if len(found) > 1:
            log.error(
                "%d transactions match one deposit's amount (%s): %s. "
                "Refusing to choose.", len(found), expected, [t.txid for t in found])
        return None
    return found[0]


# The margin added before a deposit's own created_at, and the label this
# rail is stored under. Binance's clock and this server's can differ by a
# couple of seconds even with sync_clock() run — the grace absorbs that
# without widening the window enough to risk matching an unrelated
# transaction from just before the request existed.
_GRACE_MS = 120_000
METHOD = "binancepay"


class BinancePaySweeper:
    """Polls YOUR OWN Binance Pay history and credits every deposit it can
    match, on a timer.

    ONE REQUEST COVERS EVERY OPEN DEPOSIT, however many there are — the
    window starts at the oldest one and ends now, so a shop with nothing
    outstanding makes no API calls at all. This is deliberately how
    ZentraShopBot's own sweep() works: the Binance Pay history endpoint is
    not free to call, and asking once for everyone is the entire point.
    """

    def __init__(self, db, client: BinancePayClient, *, lookback_minutes: int = 180,
                 sweep_seconds: float = 20.0, on_credit=None):
        self.db = db
        self.client = client
        self.lookback_minutes = lookback_minutes
        self.sweep_seconds = sweep_seconds
        self.on_credit = on_credit
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        log.info("Binance Pay sweeper started: uid=%s", self.client.uid)
        while not self._stop.is_set():
            try:
                await self.run_once()
            except BinanceError as exc:
                log.warning("Binance Pay sweep: %s", exc)
            except Exception:  # noqa: BLE001
                log.exception("Binance Pay sweep: unexpected error, will retry.")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.sweep_seconds)
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> int:
        await self.db.expire_deposits()

        # AWAITING AND EXPIRED-BUT-COOLING, both — not just 'awaiting'.
        # NOT because a payment made AFTER expiry gets credited here: it
        # cannot — matching_transfer()'s closed_ms is the deposit's own
        # expires_at, unconditionally, on purpose (a Binance Pay request
        # that has lapsed is lapsed; the cooldown exists only to stop the
        # amount being handed to someone ELSE while a genuinely late
        # payment is sorted out by hand). This wider status list exists
        # for the boundary case: a payment sent a moment BEFORE expiry,
        # on a deposit expire_deposits() happened to flip to 'expired'
        # microseconds earlier in the same sweep. Excluding it there would
        # be excluding a payment that was never actually late.
        open_deposits = await self.db.fetchall(
            "SELECT * FROM deposits WHERE method = %s "
            "  AND status IN ('awaiting', 'expired') AND tx_hash IS NULL "
            "  AND cooldown_until > now() "
            "ORDER BY created_at",
            (METHOD,),
        )
        if not open_deposits:
            return 0

        now_ms = int(time.time() * 1000)
        oldest_ms = int(open_deposits[0]["created_at"].timestamp() * 1000) - _GRACE_MS
        start_ms = max(oldest_ms, now_ms - self.lookback_minutes * 60_000)

        rows = await self.client.transactions(start_ms, now_ms)

        credited_count = 0
        for deposit in open_deposits:
            opened_ms = int(deposit["created_at"].timestamp() * 1000) - _GRACE_MS
            closed_ms = int(deposit["expires_at"].timestamp() * 1000)
            transfer = matching_transfer(
                rows, expected=Decimal(str(deposit["amount_expected"])),
                own_uid=self.client.uid, opened_ms=opened_ms, closed_ms=closed_ms,
            )
            if transfer is None:
                continue

            credited = await self.db.credit_deposit(deposit["id"], tx_hash=transfer.txid)
            if credited is not None:
                credited_count += 1
                if self.on_credit is not None:
                    await self.on_credit(credited)

        return credited_count
