"""A single-endpoint JSON-RPC client for BSC.

DELIBERATELY SIMPLER THAN ZENTRASHOPBOT'S OWN. That bot runs a pool of RPC
providers with automatic failover and an event-driven WebSocket
subscription — because at real production volume, one provider's outage
must not mean missed payments. This starter uses one HTTP endpoint and
polls it every few seconds instead. That is a real tradeoff, made on
purpose: requiring a reseller to configure multiple RPC providers with
failover before their FIRST payment can be accepted is exactly the kind of
requirement that keeps a starter kit from ever being finished. Add a
second provider later if one endpoint's uptime is not enough for you — the
watcher takes the address of the code that decides what "current" means to
change without needing to be rewritten around it.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from .abi import DECIMALS_SELECTOR, parse_hex

log = logging.getLogger(__name__)


class RpcError(RuntimeError):
    """A JSON-RPC call failed."""


class BscRpc:
    def __init__(self, http_url: str, *, timeout: float = 20.0, retries: int = 2):
        self._http_url = http_url
        self._retries = max(0, retries)
        self._http = httpx.AsyncClient(timeout=timeout)
        self._id = 0
        self._decimals: dict[str, int] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def call(self, method: str, params: list | None = None):
        if not self._http_url:
            raise RpcError("No BSC HTTP RPC endpoint configured (BSC_HTTP_URL).")

        last_error: Exception | None = None
        for attempt in range(self._retries + 1):
            self._id += 1
            payload = {"jsonrpc": "2.0", "id": self._id, "method": method,
                      "params": params or []}
            try:
                response = await self._http.post(self._http_url, json=payload)
                response.raise_for_status()
                body = response.json()
                if "error" in body:
                    # The node answered — retrying will not change its mind.
                    raise RpcError(f"{method}: {body['error'].get('message', 'error')}")
                return body.get("result")
            except RpcError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < self._retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
        raise RpcError(f"{method} failed after {self._retries + 1} attempts: {last_error}")

    async def block_number(self) -> int:
        return parse_hex(await self.call("eth_blockNumber"))

    async def get_logs(self, *, from_block: int, to_block: int, address: str,
                       topics: list) -> list[dict]:
        result = await self.call("eth_getLogs", [{
            "fromBlock": hex(from_block), "toBlock": hex(to_block),
            "address": address, "topics": topics,
        }])
        return result or []

    async def decimals(self, token_address: str) -> int:
        """decimals() on the token contract, cached forever — it cannot
        change for a deployed token, so asking twice is pure waste."""
        key = token_address.lower()
        if key not in self._decimals:
            result = await self.call(
                "eth_call", [{"to": token_address, "data": DECIMALS_SELECTOR}, "latest"])
            self._decimals[key] = int(result, 16) if result else 18
        return self._decimals[key]
