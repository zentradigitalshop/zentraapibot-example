"""Minimal ABI handling for BEP-20 Transfer logs.

Ported near-verbatim from ZentraShopBot's own zentra/chain/abi.py — this
part of the on-chain code was already battle-tested there, and there is no
reason for the arithmetic or the log-decoding logic to be any different
here. Only what this starter needs, so there is no web3 dependency to
install, pin and keep current for the sake of two hex conversions.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, NamedTuple

# keccak256("Transfer(address,address,uint256)") — the standard ERC-20/BEP-20
# Transfer event. Every compliant token emits this same topic, which is why
# the token CONTRACT ADDRESS, never the symbol, identifies what was paid.
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# eth_call selector for decimals() — keccak256("decimals()")[:4].
DECIMALS_SELECTOR = "0x313ce567"


class Transfer(NamedTuple):
    """A decoded Transfer log."""

    token: str        # contract that emitted it, lowercased
    sender: str       # lowercased
    recipient: str    # lowercased
    value: int        # integer base units — never a float
    tx_hash: str
    log_index: int
    block_number: int


def topic_to_address(topic: str) -> str:
    """An indexed address topic is 32 bytes; the address is the last 20."""
    return "0x" + topic[-40:].lower()


def address_to_topic(address: str) -> str:
    """Left-pad an address to 32 bytes for use in a log filter."""
    clean = address.lower().removeprefix("0x")
    return "0x" + clean.rjust(64, "0")


def decode_transfer(log: dict[str, Any]) -> Transfer | None:
    """Decode a Transfer log, or return None if it is not one.

    Returns None rather than raising: a malformed or unexpected log arriving
    from a public RPC endpoint is routine, and must never take the watcher
    down.
    """
    try:
        topics = log.get("topics") or []
        if len(topics) < 3 or topics[0].lower() != TRANSFER_TOPIC:
            return None

        data = (log.get("data") or "0x").strip()
        # A compliant Transfer carries exactly one 32-byte non-indexed value.
        value = int(data, 16) if data not in ("", "0x") else 0

        return Transfer(
            token=(log.get("address") or "").lower(),
            sender=topic_to_address(topics[1]),
            recipient=topic_to_address(topics[2]),
            value=value,
            tx_hash=(log.get("transactionHash") or "").lower(),
            log_index=parse_hex(log.get("logIndex")),
            block_number=parse_hex(log.get("blockNumber")),
        )
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        return None


def to_units(amount: Decimal, decimals: int) -> int:
    """Human amount -> integer base units.

    Comparison of crypto amounts happens in integers only. 10.50 == received
    is a bug waiting to happen; 10500000000000000000 == received is not.
    """
    scaled = amount * (Decimal(10) ** decimals)
    # Quantize before int() so 0.1 * 10**18 cannot land a unit low.
    return int(scaled.quantize(Decimal(1)))


def from_units(units: int, decimals: int) -> Decimal:
    """Integer base units -> human amount, for display and for matching
    against amount_expected — which is why this must be exact Decimal
    division, never a float divide."""
    return Decimal(units) / (Decimal(10) ** decimals)


def parse_hex(value: Any, default: int = 0) -> int:
    """Parse a JSON-RPC quantity, which may be a hex string or already an int."""
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value:
        try:
            return int(value, 16)
        except ValueError:
            return default
    return default
