"""The JSON-RPC client: request shape, retries, and the decimals() cache.

httpx.MockTransport stands in for the RPC node — no real network. Run:
python -m tests.test_chain_rpc
"""

from __future__ import annotations

import asyncio

import httpx

from bot.chain.rpc import BscRpc, RpcError


async def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    # ---- a normal call -----------------------------------------------------

    print("\nA normal call")

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x64"})

    rpc = BscRpc("https://fake-rpc.example")
    rpc._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await rpc.block_number() == 100
    assert seen["body"]["method"] == "eth_blockNumber"
    ok("eth_blockNumber parses the hex result into an int")

    # ---- a JSON-RPC error is NOT retried -----------------------------------------------------

    print("\nA JSON-RPC error")

    calls = {"n": 0}

    def error_handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={
            "jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "bad request"}})

    rpc2 = BscRpc("https://fake-rpc.example", retries=3)
    rpc2._http = httpx.AsyncClient(transport=httpx.MockTransport(error_handler))
    try:
        await rpc2.call("eth_blockNumber")
        raised = False
    except RpcError as exc:
        raised = True
        assert "bad request" in str(exc)
    assert raised
    assert calls["n"] == 1, "a JSON-RPC error was retried, but the node already answered"
    ok("the node answering with an error is not retried — retrying would not change its mind")

    # ---- a transport failure IS retried, then gives up -----------------------------------------------------

    print("\nA transport failure")

    tries = {"n": 0}

    def flaky_handler(request: httpx.Request) -> httpx.Response:
        tries["n"] += 1
        raise httpx.ConnectError("connection refused")

    rpc3 = BscRpc("https://fake-rpc.example", retries=2)
    rpc3._http = httpx.AsyncClient(transport=httpx.MockTransport(flaky_handler))
    try:
        await rpc3.call("eth_blockNumber")
        raised = False
    except RpcError:
        raised = True
    assert raised
    assert tries["n"] == 3, f"expected 1 + 2 retries, got {tries['n']}"
    ok("a connection failure is retried up to the configured limit, then gives up")

    # ---- decimals() is cached -----------------------------------------------------

    print("\nCaching decimals()")

    decimals_calls = {"n": 0}

    def decimals_handler(request: httpx.Request) -> httpx.Response:
        decimals_calls["n"] += 1
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x12"})  # 18

    rpc4 = BscRpc("https://fake-rpc.example")
    rpc4._http = httpx.AsyncClient(transport=httpx.MockTransport(decimals_handler))
    d1 = await rpc4.decimals("0xTOKEN")
    d2 = await rpc4.decimals("0xTOKEN")
    d3 = await rpc4.decimals("0xtoken")  # same token, different case
    assert d1 == d2 == d3 == 18
    assert decimals_calls["n"] == 1, "decimals() was fetched more than once for the same token"
    ok("decimals() is fetched once per token and never asked again — it cannot change")

    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
