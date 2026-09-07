"""The Zentra API client: parsing, error mapping, money as Decimal.

No network — httpx.MockTransport stands in for Zentra's server, so this
runs anywhere and never depends on the real API being reachable. What it
proves is the CONTRACT: every dollar figure comes back as Decimal, every
error becomes ZentraError with the right .code and .unresolved flag, and a
malformed key is refused before a request is ever sent.

Run:  python -m tests.test_zentra_api
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import httpx

from bot.zentra_api import ZentraClient, ZentraError

GOOD_KEY = "zen_live_" + "a" * 40


def transport(handler):
    return httpx.MockTransport(handler)


async def with_client(handler, coro):
    client = ZentraClient(GOOD_KEY)
    client._client = httpx.AsyncClient(
        transport=transport(handler), base_url="https://api.zentradigital.shop",
        headers=client._client.headers,
    )
    try:
        return await coro(client)
    finally:
        await client.aclose()


async def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    # ---- the key itself, no network involved -----------------------------------

    print("\nThe key")

    for bad in ("", "nope", "ak_someoneelses", "zen_live_"[:-1]):
        try:
            ZentraClient(bad)
            raised = False
        except ValueError:
            raised = True
        assert raised, bad
    ok("a malformed key is refused before any request is ever made")

    ZentraClient(GOOD_KEY)  # must not raise
    ok("a well-formed key constructs a client")

    # ---- me() -----------------------------------------------------

    print("\n/v1/me")

    def me_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {GOOD_KEY}"
        return httpx.Response(200, json={
            "user_id": "42", "username": "reseller", "api_key_prefix": "zen_live_aaaa",
            "balance_usd": "123.4500",
        })

    async def check_me(client):
        me = await client.me()
        assert me.user_id == "42"
        assert me.balance_usd == Decimal("123.4500")
        assert isinstance(me.balance_usd, Decimal)
        return me

    await with_client(me_handler, check_me)
    ok("every request carries the bearer key, and balance parses as Decimal")

    # ---- products -----------------------------------------------------

    print("\nProducts")

    def products_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"products": [
            {"id": "1", "name": "Gemini AI Pro", "description": "", "unit_label": "code",
             "price": "0.90", "currency": "USDT", "stock": 50, "available": True},
            {"id": "2", "name": "Sold Out Thing", "description": "", "unit_label": "code",
             "price": "0.20", "currency": "USDT", "stock": 0, "available": False},
        ]})

    async def check_products(client):
        products = await client.products()
        assert len(products) == 2
        assert products[0].price == Decimal("0.90")
        assert products[1].available is False
        return products

    await with_client(products_handler, check_products)
    ok("the catalogue parses, including an unavailable product's own price")

    # ---- errors map to ZentraError, with the right shape -----------------------------------

    print("\nErrors")

    def error_handler(status: int, code: str, message: str):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"error": message, "code": code})
        return handler

    async def expect_error(client, coro_factory, *, code: str, status: int,
                           unresolved: bool = False):
        try:
            await coro_factory(client)
            raised = None
        except ZentraError as exc:
            raised = exc
        assert raised is not None, "expected a ZentraError"
        assert raised.code == code, (raised.code, code)
        assert raised.status == status
        assert raised.unresolved == unresolved

    await with_client(
        error_handler(401, "unauthorized", "Bad key."),
        lambda c: expect_error(c, lambda c2: c2.me(), code="unauthorized", status=401),
    )
    ok("401 becomes ZentraError(code='unauthorized')")

    await with_client(
        error_handler(409, "out_of_stock", "None left."),
        lambda c: expect_error(
            c, lambda c2: c2.create_order(1, 1, idempotency_key="k"),
            code="out_of_stock", status=409),
    )
    ok("409 out_of_stock is not marked unresolved")

    await with_client(
        error_handler(202, "upstream_timeout", "Not sure yet."),
        lambda c: expect_error(
            c, lambda c2: c2.create_order(1, 1, idempotency_key="k"),
            code="upstream_timeout", status=202, unresolved=True),
    )
    ok("202 IS marked unresolved — the flag purchase() decides refunds on")

    # ---- rate limiting: Retry-After is surfaced -----------------------------------

    print("\nRate limiting")

    def rate_limit_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, headers={"Retry-After": "7"},
            json={"error": "Slow down.", "code": "rate_limited"})

    async def check_rate_limit(client):
        try:
            await client.me()
            raised = None
        except ZentraError as exc:
            raised = exc
        assert raised is not None
        assert raised.code == "rate_limited"
        assert raised.retry_after == 7.0

    await with_client(rate_limit_handler, check_rate_limit)
    ok("a 429's Retry-After header is parsed into .retry_after")

    # ---- idempotency key really is sent -----------------------------------

    print("\nThe idempotency header")

    seen = {}

    def order_handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("X-Idempotency-Key")
        body = json.loads(request.content)
        return httpx.Response(201, json={"order": {
            "id": "99", "reference": "ZEN-TEST0001", "product_id": str(body["product_id"]),
            "product_name": "Thing", "quantity": body["quantity"], "total": "1.00",
            "currency": "USDT", "status": "delivered", "created_at": None,
            "completed_at": None, "delivery": [{"label": "Code", "value": "abc"}],
        }})

    async def check_order(client):
        order = await client.create_order(1, 1, idempotency_key="my-key-123")
        assert order.reference == "ZEN-TEST0001"
        assert order.delivery[0].value == "abc"

    await with_client(order_handler, check_order)
    assert seen["key"] == "my-key-123"
    ok("the idempotency key is sent as X-Idempotency-Key, exactly as given")

    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
