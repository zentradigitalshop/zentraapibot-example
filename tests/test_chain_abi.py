"""Decoding a Transfer log, and the integer/Decimal conversions around it.

No network, no database. Run:  python -m tests.test_chain_abi
"""

from __future__ import annotations

from decimal import Decimal

from bot.chain.abi import (
    TRANSFER_TOPIC, address_to_topic, decode_transfer, from_units,
    parse_hex, to_units, topic_to_address,
)

ADDRESS = "0x1111111111111111111111111111111111111111"


def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    # ---- addresses <-> topics -----------------------------------------------------

    topic = address_to_topic(ADDRESS)
    assert len(topic) == 66  # 0x + 64 hex chars
    assert topic_to_address(topic) == ADDRESS.lower()
    ok("an address survives the round trip through a 32-byte topic")

    # ---- unit conversion, exactly -----------------------------------------------------

    assert to_units(Decimal("1.00"), 18) == 1_000_000_000_000_000_000
    assert from_units(1_000_000_000_000_000_000, 18) == Decimal("1.00")
    ok("1.00 USDT is exactly 10^18 base units, both directions")

    tail_amount = Decimal("5.1234")
    units = to_units(tail_amount, 18)
    back = from_units(units, 18)
    assert back == tail_amount, (back, tail_amount)
    ok("a four-decimal tail survives the round trip exactly — no float ever involved")

    # THE precision claim this whole matching scheme depends on: comparing
    # a Decimal built from on-chain integer units against a Decimal the
    # allocator stored is exact, not approximate, because dividing an
    # integer by an exact power of ten never loses precision in Decimal
    # arithmetic (within the default 28-digit context).
    expected = Decimal("9999.0099")
    received = from_units(to_units(expected, 18), 18)
    assert received == expected
    ok("(the matching guarantee) large realistic amounts round-trip exactly too")

    # ---- decoding a real-shaped Transfer log -----------------------------------------------------

    print("\nDecoding")

    recipient_topic = address_to_topic(ADDRESS)
    sender_topic = address_to_topic("0x2222222222222222222222222222222222222222")
    raw = {
        "address": "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        "topics": [TRANSFER_TOPIC, sender_topic, recipient_topic],
        "data": hex(5_123_400_000_000_000_000),
        "transactionHash": "0xDEAD",
        "logIndex": "0x2",
        "blockNumber": "0x64",
    }
    t = decode_transfer(raw)
    assert t is not None
    assert t.recipient == ADDRESS.lower()
    assert t.sender == "0x2222222222222222222222222222222222222222"
    assert t.value == 5_123_400_000_000_000_000
    assert t.log_index == 2
    assert t.block_number == 100
    assert t.tx_hash == "0xdead"
    ok("a well-formed Transfer log decodes into every field correctly")

    assert decode_transfer({"topics": [], "data": "0x"}) is None
    ok("a log with no topics is not a Transfer — returns None, never raises")

    assert decode_transfer({"topics": ["0xnotthetransfertopic", "a", "b"]}) is None
    ok("a log with the wrong topic[0] is not a Transfer")

    assert decode_transfer({"topics": [TRANSFER_TOPIC, "a"]}) is None
    ok("a log missing the recipient topic does not raise — decoding just refuses it")

    assert decode_transfer("not even a dict") is None
    ok("garbage input does not raise either — a malformed log is routine on a public RPC")

    # ---- hex parsing -----------------------------------------------------

    print("\nHex parsing")

    assert parse_hex("0x10") == 16
    assert parse_hex(10) == 10
    assert parse_hex(None) == 0
    assert parse_hex("not hex", default=99) == 99
    ok("parse_hex handles a hex string, an already-int value, None, and garbage")

    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    main()
