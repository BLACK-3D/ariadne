"""Fixed optional MCP client worker. No server launching or application authority."""
from __future__ import annotations

import asyncio
import importlib.metadata
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
CALL_SUBMITTED = False


def _client_class():
    # Explicitly disable entry-point/internal extensions even if a future
    # environment contains optional task packages. The SDK is worker-only.
    from fastmcp import Client

    class BoundClient(Client):
        _auto_internal_extensions = False

    return BoundClient


async def operate(request):
    global CALL_SUBMITTED
    from ariadne.mcp_tools import (DEADLINE, MAX_DISCOVERED, MAX_OUTPUT, WIRE_LIMIT,
                                   _bounded, canonical, validate_url)
    from fastmcp.client.transports import StreamableHttpTransport
    import httpx2

    url = validate_url(request["url"])
    budget = [0]

    class BoundedStream(httpx2.AsyncByteStream):
        def __init__(self, source):
            self.source = source

        async def __aiter__(self):
            async for chunk in self.source:
                budget[0] += len(chunk)
                if budget[0] > WIRE_LIMIT:
                    raise ValueError("MCP wire budget exceeded.")
                yield chunk

        async def aclose(self):
            await self.source.aclose()

    class FixedTransport(httpx2.AsyncBaseTransport):
        def __init__(self):
            self.base = httpx2.AsyncHTTPTransport(retries=0)

        async def handle_async_request(self, outgoing):
            if str(outgoing.url) != url or outgoing.method not in {"POST", "GET", "DELETE"}:
                raise ValueError("MCP attempted an unregistered destination.")
            response = await self.base.handle_async_request(outgoing)
            if (300 <= response.status_code < 400
                    or response.headers.get("content-encoding", "identity").lower() not in {"identity", ""}):
                await response.aclose()
                raise ValueError("MCP redirects and compressed responses are unsupported.")
            size = response.headers.get("content-length")
            if size is not None and (not size.isdecimal() or int(size) > WIRE_LIMIT):
                await response.aclose()
                raise ValueError("MCP response exceeds its limit.")
            response.stream = BoundedStream(response.stream)
            return response

        async def aclose(self):
            await self.base.aclose()

    def factory(**kwargs):
        # FastMCP currently asks factories to follow redirects; deliberately override.
        headers = {**(kwargs.get("headers") or {}), "Accept-Encoding": "identity"}
        return httpx2.AsyncClient(headers=headers, auth=None, follow_redirects=False,
                                 trust_env=False, timeout=httpx2.Timeout(5), transport=FixedTransport())

    async def refuse(*args, **kwargs):
        raise ValueError("Server-initiated requests are unsupported.")

    async def discard(*args, **kwargs):
        return None

    transport = StreamableHttpTransport(url=url, headers=request["headers"],
                                        httpx_client_factory=factory, max_sse_event_size=MAX_OUTPUT)
    modern = request["mode"] == "modern"
    client = _client_class()(transport, mode="2026-07-28" if modern else "legacy", timeout=8, init_timeout=5,
                    cache=False, roots=[], sampling_handler=refuse, elicitation_handler=refuse,
                    log_handler=discard, progress_handler=discard, input_required_max_rounds=0,
                    extensions=[])
    async with asyncio.timeout(DEADLINE - 2):
        async with client:
            version = "2026-07-28" if modern else "2025-11-25"
            if client.protocol_version != version:
                raise ValueError("MCP protocol changed or is unsupported.")
            if modern:
                discovered = await client.session.send_discover(version)
                if version not in discovered.get("supportedVersions", []):
                    raise ValueError("MCP server does not declare the configured version.")
                identity = {key: value for key, value in discovered.items() if key not in {"ttlMs", "cacheScope"}}
            else:
                identity = {"protocolVersion": version,
                            "serverInfo": client.server_info.model_dump(by_alias=True, mode="json", exclude_none=True),
                            "capabilities": client.server_capabilities.model_dump(by_alias=True, mode="json", exclude_none=True),
                            "instructions": client.instructions}
            _bounded(identity, 16_000)
            # Reject partial pagination instead of trusting an incomplete snapshot.
            listing = await client.list_tools_mcp(cache_mode="bypass")
            if listing.next_cursor is not None or len(listing.tools) > MAX_DISCOVERED:
                raise ValueError("MCP pagination/tool limit exceeded.")
            tools = [tool.model_dump(by_alias=True, mode="json", exclude_none=True) for tool in listing.tools]
            names = [tool.get("name") for tool in tools]
            if len(set(names)) != len(names):
                raise ValueError("MCP tool names are ambiguous.")
            if request["operation"] == "discover":
                result = {"identity": identity, "tools": tools}
                _bounded(result, 240_000)
                return result
            expected = request["expected_tool"]
            actual = next((tool for tool in tools if tool.get("name") == request["name"]), None)
            if canonical(identity) != canonical(request["expected_identity"]) or canonical(actual) != canonical(expected):
                raise ValueError("MCP metadata changed; owner re-registration required.")
            # Raw MCP result avoids FastMCP's higher-level schema-to-Python conversion.
            CALL_SUBMITTED = True
            raw = await client.call_tool_mcp(request["name"], request["arguments"], timeout=8)
            value = raw.model_dump(by_alias=True, mode="json", exclude_none=True)
            _bounded(value, MAX_OUTPUT)
            if value.get("isError") or value.get("resultType", "complete") != "complete":
                raise ValueError("MCP tool failed or requested unsupported continuation.")
            blocks = value.get("content", [])
            if not isinstance(blocks, list) or len(blocks) > 32:
                raise ValueError("MCP output block limit exceeded.")
            text = []
            for block in blocks:
                if block.get("type") != "text" or not isinstance(block.get("text"), str):
                    raise ValueError("Only bounded text/JSON tool output is supported.")
                text.append({"type": "text", "text": block["text"]})
            result = {"content": text}
            if "structuredContent" in value:
                result["structured_content"] = value["structuredContent"]
            _bounded(result, MAX_OUTPUT)
            return result


def main():
    try:
        from ariadne.worker import apply_limits
        apply_limits(memory_bytes=384 * 1024 * 1024, cpu_seconds=10)
        raw = sys.stdin.buffer.read(300_001)
        if len(raw) > 300_000:
            raise ValueError("Worker input exceeded its limit.")
        if importlib.metadata.version("fastmcp-slim") != "4.1.4":
            raise ValueError("Pinned SDK missing.")
        from ariadne.mcp_tools import parse_json, canonical
        request = parse_json(raw)
        logging.disable(logging.CRITICAL)
        result = asyncio.run(operate(request))
        output = canonical(result)
        if len(output) > 262_144:
            raise ValueError("Worker output exceeded its limit.")
        sys.stdout.buffer.write(output)
        return 0
    except BaseException:
        # No server text, header, URL, exception detail, or traceback crosses this boundary.
        sys.stdout.buffer.write(json.dumps({"error": "MCP worker rejected the operation or exceeded a limit.",
                                            "submitted": CALL_SUBMITTED}).encode("ascii"))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
