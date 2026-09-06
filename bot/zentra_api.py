"""A client for the Zentra Reseller API.

This is the one file in this whole starter that has to be RIGHT rather than
merely working — every rail, every screen, every dashboard number eventually
comes from a call made here. So it does three things and nothing else:

  * signs every request with your Zentra API key
  * turns Zentra's JSON errors into one Python exception you can catch once
  * keeps every amount a Decimal, parsed from the string Zentra sends —
    never a float, because floats cannot hold money exactly and a rounding
    error a customer can see is a rounding error a customer will report

It does NOT decide what to sell, what to charge your own customers, or how
to take their money. Those are this bot's decisions, made elsewhere, against
your own database. This file only talks to Zentra.

Full reference: https://zentradigital.shop/api/docs
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

log = logging.getLogger(__name__)


class ZentraError(Exception):
    """A request to Zentra was refused or failed.

    `code` is the stable machine-readable string from the response body
    (`"insufficient_balance"`, `"out_of_stock"`, ...) — branch on this, never
    on `message`, which is prose Zentra can reword without warning.

    `status` is the HTTP status. Two of them mean something beyond "it
    failed" and are given their own flag rather than left for the caller to
    remember as magic numbers:

      * `unresolved` (HTTP 202) — Zentra took your money and the order may or
        may not have gone through; a human needs to look, and you must NOT
        retry this purchase automatically. See `purchase()` for the one
        thing this changes in your own bookkeeping.
      * `rate_limited` (HTTP 429) — back off. `retry_after` is seconds, when
        Zentra sent one; otherwise guess a few seconds and try again.
    """

    def __init__(self, message: str, *, code: str, status: int,
                 unresolved: bool = False, retry_after: float | None = None):
        super().__init__(f"{code}: {message}")
        self.message = message
        self.code = code
        self.status = status
        self.unresolved = unresolved
        self.retry_after = retry_after


@dataclass(frozen=True)
class Me:
    user_id: str
    username: str | None
    api_key_prefix: str
    balance_usd: Decimal


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    description: str
    unit_label: str
    price: Decimal
    currency: str
    stock: int | None
    available: bool


@dataclass(frozen=True)
class DeliveryField:
    label: str
    value: str


@dataclass(frozen=True)
class Order:
    id: str
    reference: str
    product_id: str
    product_name: str | None
    quantity: int
    total: Decimal
    currency: str
    status: str
    created_at: str | None
    completed_at: str | None
    delivery: list[DeliveryField] | None


def _decimal(value: Any) -> Decimal:
    """Every money field arrives as a JSON string. Parsed from the string,
    never from a float that already lost precision converting it."""
    return Decimal(str(value))


def _product(row: dict) -> Product:
    return Product(
        id=str(row["id"]), name=row["name"], description=row.get("description") or "",
        unit_label=row.get("unit_label") or "item", price=_decimal(row["price"]),
        currency=row.get("currency", "USDT"), stock=row.get("stock"),
        available=bool(row.get("available")),
    )


def _order(row: dict) -> Order:
    delivery = None
    if row.get("delivery"):
        delivery = [DeliveryField(d["label"], d["value"]) for d in row["delivery"]]
    return Order(
        id=str(row["id"]), reference=row["reference"], product_id=str(row["product_id"]),
        product_name=row.get("product_name"), quantity=int(row["quantity"]),
        total=_decimal(row["total"]), currency=row.get("currency", "USDT"),
        status=row["status"], created_at=row.get("created_at"),
        completed_at=row.get("completed_at"), delivery=delivery,
    )


class ZentraClient:
    """One instance per process, reused across requests.

    Holds a connection pool (httpx does this internally), not a session in
    the login sense — every request carries the same bearer key, there is
    nothing to log in or out of.
    """

    def __init__(self, api_key: str, *, base_url: str = "https://api.zentradigital.shop",
                 timeout: float = 30.0):
        if not api_key or not api_key.startswith("zen_live_"):
            raise ValueError(
                "That does not look like a Zentra API key. Get one from "
                "@ZentraShopBot — Menu → API Link → Create API key."
            )
        self._key = api_key
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "ZentraClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _request(self, method: str, path: str, *,
                       json: dict | None = None,
                       headers: dict | None = None,
                       params: dict | None = None) -> dict:
        try:
            response = await self._client.request(
                method, path, json=json, headers=headers, params=params)
        except httpx.TimeoutException as exc:
            # A timeout on a GET is safe to just fail. A timeout on POST
            # /orders is NOT — see purchase() below, which is the only
            # caller that treats this specially.
            raise ZentraError(
                "The request to Zentra timed out.", code="timeout", status=0,
            ) from exc
        except httpx.HTTPError as exc:
            raise ZentraError(
                f"Could not reach Zentra: {exc}", code="network_error", status=0,
            ) from exc

        if response.status_code == 429:
            retry_after = None
            header = response.headers.get("Retry-After")
            if header:
                try:
                    retry_after = float(header)
                except ValueError:
                    pass
            body = _safe_json(response)
            raise ZentraError(
                (body or {}).get("error", "Too many requests."),
                code=(body or {}).get("code", "rate_limited"),
                status=429, retry_after=retry_after,
            )

        # 202 is Zentra's OWN error shape, not a success with a odd status —
        # it means the order may or may not have gone through, and the body
        # is {"error": ..., "code": ...}, exactly like a 4xx. A body-shape
        # check here (rather than trusting the 2xx range) is what a test
        # firing a mocked 202 at create_order() catches if this drifts:
        # without it, this line parses an error body as an Order and raises
        # a confusing KeyError instead of the ZentraError callers expect.
        if response.status_code >= 400 or response.status_code == 202:
            body = _safe_json(response) or {}
            raise ZentraError(
                body.get("error", f"Zentra returned HTTP {response.status_code}."),
                code=body.get("code", "unknown_error"),
                status=response.status_code,
                unresolved=(response.status_code == 202),
            )

        return _safe_json(response) or {}

    # ---- account -----------------------------------------------------------

    async def me(self) -> Me:
        body = await self._request("GET", "/v1/me")
        return Me(
            user_id=str(body["user_id"]), username=body.get("username"),
            api_key_prefix=body["api_key_prefix"], balance_usd=_decimal(body["balance_usd"]),
        )

    # ---- catalogue -----------------------------------------------------------

    async def products(self) -> list[Product]:
        """Every product Zentra sells, whether or not it currently has stock.

        Check `.available` before offering one, and re-check right before
        placing an order — stock can run out between the two, which is
        exactly why `create_order` can still answer `out_of_stock`.
        """
        body = await self._request("GET", "/v1/products")
        return [_product(row) for row in body.get("products", body if isinstance(body, list) else [])]

    async def product(self, product_id: str | int) -> Product:
        body = await self._request("GET", f"/v1/products/{product_id}")
        return _product(body)

    # ---- orders -----------------------------------------------------------

    async def create_order(self, product_id: str | int, quantity: int, *,
                           idempotency_key: str) -> Order:
        """Place an order. Charges YOUR Zentra wallet, delivers instantly.

        `idempotency_key` IS NOT OPTIONAL IN PRACTICE, even though Zentra's
        API accepts a request without one. Send the SAME key on a retry —
        after a timeout, after a 5xx, after your own process restarts mid
        request — and Zentra returns the ORIGINAL order rather than placing
        a second one. Make it once per attempt-at-a-purchase and store it
        before you send the request, not after, or a crash between "made
        the key" and "sent the request" reintroduces exactly the double
        charge this exists to prevent.

        A `ZentraError` with `.unresolved = True` means Zentra was not sure
        the order went through — the call to their own supplier died in
        transit. YOUR WALLET MAY HAVE BEEN CHARGED. Do not retry with a new
        idempotency key; that risks a second charge for one delivery. Retry
        the SAME key later — Zentra will tell you the real outcome once it
        knows — or hold the order for a human.
        """
        body = await self._request(
            "POST", "/v1/orders",
            json={"product_id": int(product_id) if str(product_id).isdigit() else product_id,
                  "quantity": quantity},
            headers={"X-Idempotency-Key": idempotency_key},
        )
        return _order(body["order"] if "order" in body else body)

    async def orders(self, *, limit: int = 50) -> list[Order]:
        body = await self._request("GET", "/v1/orders", params={"limit": limit})
        return [_order(row) for row in body.get("orders", body if isinstance(body, list) else [])]

    async def order(self, order_id: str | int) -> Order:
        body = await self._request("GET", f"/v1/orders/{order_id}")
        return _order(body["order"] if "order" in body else body)

    async def export_order(self, order_id: str | int) -> str:
        """The delivered goods as plain text — one field per line.

        For an order that has not delivered, Zentra answers `not_delivered`
        rather than an empty file; there is nothing to export yet.
        """
        response = await self._client.get(f"/v1/orders/{order_id}/export")
        if response.status_code >= 400:
            body = _safe_json(response) or {}
            raise ZentraError(
                body.get("error", f"Zentra returned HTTP {response.status_code}."),
                code=body.get("code", "unknown_error"), status=response.status_code,
            )
        return response.text


def _safe_json(response: httpx.Response) -> dict | None:
    try:
        return response.json()
    except ValueError:
        return None
