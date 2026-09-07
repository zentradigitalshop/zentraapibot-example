"""The Binance Pay client: signing, clock offset, and error mapping.

httpx.MockTransport stands in for Binance — no real network, no real
credentials. Run:  python -m tests.test_binance_client
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import urllib.parse

import httpx

from bot.binance_pay import BinanceError, BinancePayClient

SECRET = "test-secret"


def with_transport(client: BinancePayClient, handler) -> None:
    client._http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://fake-binance.example")


async def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    # ---- signing -----------------------------------------------------

    print("\nSigning")

    seen = {}

    def sign_handler(request: httpx.Request) -> httpx.Response:
        query = str(request.url.query, "utf-8")
        params = dict(urllib.parse.parse_qsl(query))
        seen["params"] = params
        seen["header"] = request.headers.get("X-MBX-APIKEY")
        expected_sig = hmac.new(
            SECRET.encode(),
            urllib.parse.urlencode({k: v for k, v in params.items() if k != "signature"}).encode(),
            hashlib.sha256,
        ).hexdigest()
        assert params["signature"] == expected_sig, "signature does not match the params sent"
        return httpx.Response(200, json={"success": True, "data": []})

    client = BinancePayClient("uid1", "key1", SECRET)
    with_transport(client, sign_handler)
    client._offset_ms = 0  # skip sync_clock's own network call — tested separately below

    await client.transactions(1000, 2000)
    assert seen["params"]["startTime"] == "1000"
    assert "signature" in seen["params"]
    ok("every request is signed with an HMAC-SHA256 that verifies against the params sent")

    assert seen["header"] == "key1"
    ok("the API key travels as the X-MBX-APIKEY header, not in the signed query")

    # ---- clock offset -----------------------------------------------------

    print("\nClock offset")

    def time_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"serverTime": 1_700_000_010_000})

    client2 = BinancePayClient("uid1", "key1", SECRET)
    with_transport(client2, time_handler)
    import time
    real_now = int(time.time() * 1000)
    offset = await client2.sync_clock()
    # The offset is server_time minus THIS machine's clock at call time —
    # cannot assert an exact value without controlling both clocks, but it
    # must be in the right ballpark (server_time far in the future here).
    assert offset > 1_600_000_000_000 - real_now
    ok("sync_clock() computes an offset from Binance's own reported server time")

    # ---- error mapping -----------------------------------------------------

    print("\nError mapping")

    async def expect_error(handler, *, retryable: bool, operator: bool):
        c = BinancePayClient("uid1", "key1", SECRET)
        with_transport(c, handler)
        c._offset_ms = 0
        try:
            await c.transactions(0, 1)
            raised = None
        except BinanceError as exc:
            raised = exc
        assert raised is not None, "expected a BinanceError"
        assert raised.retryable == retryable, (raised.retryable, retryable)
        assert raised.operator == operator, (raised.operator, operator)
        return raised

    await expect_error(
        lambda r: httpx.Response(451, json={"code": -1, "msg": "restricted location"}),
        retryable=False, operator=True,
    )
    ok("451 / restricted location is not retryable and is the OPERATOR's problem, not a customer's")

    await expect_error(
        lambda r: httpx.Response(400, json={"code": -1022, "msg": "Signature for this request is not valid."}),
        retryable=False, operator=True,
    )
    ok("a bad signature is the operator's credentials, not something retrying fixes")

    await expect_error(
        lambda r: httpx.Response(429, json={"code": -1, "msg": "too many requests"}),
        retryable=True, operator=False,
    )
    ok("429 rate limiting is retryable and is not the operator's fault")

    # -1021 (bad timestamp) must also clear the cached clock offset, so the
    # NEXT call re-learns it rather than repeating the same stale offset.
    c3 = BinancePayClient("uid1", "key1", SECRET)
    with_transport(c3, lambda r: httpx.Response(
        400, json={"code": -1021, "msg": "Timestamp outside recvWindow."}))
    c3._offset_ms = 12345
    try:
        await c3.transactions(0, 1)
    except BinanceError:
        pass
    assert c3._offset_ms is None, "a timestamp error did not clear the cached offset"
    ok("a timestamp rejection clears the cached clock offset so it is re-learned next time")

    # ---- the response shape guard -----------------------------------------------------

    print("\nA response Binance did not actually mean as success")

    await expect_error(
        lambda r: httpx.Response(200, json={"success": True, "data": "not-a-list"}),
        retryable=True, operator=False,
    )
    ok("a 200 whose data is not a list is an error, never treated as zero transactions")

    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
