"""``guard mcp serve --stdio``: the MCP server (M5, docs/09 "MCP integration"; optional extra ``mcp``).

A thin adapter from the official MCP Python SDK (``mcp``, pinned in the ``mcp`` extra) to
``service.AgentService``, which calls the same orchestration code as the CLI.

Protocol hygiene (T50): stdout carries only JSON-RPC frames. While serving, the SDK points
fd 1 at stderr and serves the wire from a private duplicate. This module additionally
rebinds ``sys.stdout`` to ``sys.stderr``, because buffered Python-level output would
otherwise be flushed onto the wire when the SDK restores fd 1 at exit (observed with a
prototype; see docs/DECISIONS.md I-091). Logs go to stderr. The SDK's default
OpenTelemetry middleware is removed (no telemetry by default).

Cancellation and timeouts (T52): each tool call runs in a worker thread with a bound
``CancelToken``. A client ``notifications/cancelled`` interrupts the handler, which
cancels the token; per protocol the cancelled request gets no response. The server-side
timeout (``--timeout-s``, or a smaller ``timeout_s`` argument) cancels the token and
returns an explicit ``TIMEOUT`` result. In both cases the engine stops at its next safe
point and the run is finalized as failed (no cache, evidence or report is published).
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from . import ENGINE_VERSION, cancellation
from .service import ERROR_STATUSES, OUTPUT_SCHEMA, TOOL_SPECS, AgentService

SERVER_NAME = "invariantguard"
INSTRUCTIONS = ("InvariantGuard verifies coding-agent patches locally. Call verify_patch(repository, base) before declaring a change complete; "
                "a BLOCK verdict carries concrete witnesses (get_finding / explain_finding give the full evidence). INCOMPLETE means required "
                "work is unknown, never safe. To propose behavioral contracts, call get_context_packet(repository, symbol) and then "
                "propose_contracts; accepted proposals are recorded INFERRED only. This server cannot approve contracts, change baselines or run "
                "arbitrary commands.")


class MCPUnavailable(Exception):
    pass


def _sdk() -> tuple[Any, Any, Any, Any]:
    try:
        import anyio
        import mcp_types as types
        from mcp.server.lowlevel import Server
        from mcp.server.stdio import stdio_server
    except ImportError as exc:
        raise MCPUnavailable(f"the MCP SDK is not installed ({exc}); install the 'mcp' extra: pip install -e '.[mcp]'") from None
    return anyio, types, Server, stdio_server


def sdk_version() -> str | None:
    try:
        from importlib.metadata import version

        return version("mcp")
    except Exception:  # noqa: BLE001
        return None


def _log(msg: str) -> None:
    sys.stderr.write(f"guard mcp: {msg}\n")
    sys.stderr.flush()


def client_info(ctx: Any) -> dict[str, str] | None:
    """The client's self-reported ``clientInfo`` (name, version) from the MCP handshake or the
    request envelope; None when the client did not identify itself. Never trusted."""
    info = None
    for holder in ("session", "connection"):
        try:
            params = getattr(getattr(ctx, holder, None), "client_params", None)
        except Exception:  # noqa: BLE001 - identity is informational only
            params = None
        info = getattr(params, "client_info", None) if params is not None else None
        if info is not None:
            break
    if info is None:
        return None
    return {"name": str(getattr(info, "name", "") or "")[:100], "version": str(getattr(info, "version", "") or "")[:100]}


def build_server(service: AgentService) -> Any:
    anyio, types, Server, _ = _sdk()

    tools = [types.Tool(name=name, description=spec["description"], input_schema=spec["input_schema"], output_schema=OUTPUT_SCHEMA,
                        annotations=types.ToolAnnotations(read_only_hint=spec["read_only"], destructive_hint=False, idempotent_hint=spec["read_only"],
                                                          open_world_hint=False))
             for name, spec in TOOL_SPECS.items()]

    async def on_list_tools(ctx: Any, params: Any) -> Any:
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(ctx: Any, params: Any) -> Any:
        name = params.name
        args = params.arguments or {}
        token = cancellation.CancelToken()
        timeout = service.timeout_for(name, args)
        t0 = time.perf_counter()
        client = client_info(ctx)

        def work() -> dict[str, Any]:
            with cancellation.bound(token):
                return service.call(name, args, client=client)

        result: dict[str, Any] | None = None
        try:
            with anyio.move_on_after(timeout):
                result = await anyio.to_thread.run_sync(work, abandon_on_cancel=True)
        except anyio.get_cancelled_exc_class():
            # Client cancellation (notifications/cancelled) or shutdown interrupted the await: stop the
            # abandoned worker at its next safe point. Per protocol no response is sent.
            token.cancel("client cancelled the request")
            _log(f"{name} -> CANCELLED by the client ({(time.perf_counter() - t0):.2f} s); the run is finalized as failed")
            raise
        except BaseException as exc:  # not expected (service.call reports errors as results); stop the worker and surface it
            token.cancel(f"handler failed: {type(exc).__name__}")
            _log(f"{name} -> handler failure {type(exc).__name__}: {exc}")
            raise
        if result is None:
            token.cancel(f"timeout after {timeout:g} s")
            result = service.stopped(name, "TIMEOUT", f"timeout after {timeout:g} s (server limit {service.max_timeout_s:g} s)")
        status = result.get("status")
        _log(f"{name} -> {status}" + (f" verdict={result.get('verdict')}" if result.get("verdict") else "") + f" ({(time.perf_counter() - t0):.2f} s)")
        text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)], structured_content=result, is_error=status in ERROR_STATUSES)

    server = Server(SERVER_NAME, version=ENGINE_VERSION, instructions=INSTRUCTIONS, on_list_tools=on_list_tools, on_call_tool=on_call_tool)
    server.middleware.clear()  # drop the SDK's default OpenTelemetry middleware: no telemetry by default
    return server


def serve_stdio(service: AgentService) -> int:
    anyio, _, _, stdio_server = _sdk()
    server = build_server(service)

    async def main() -> None:
        async with stdio_server() as (read_stream, write_stream):
            saved = sys.stdout
            sys.stdout = sys.stderr  # stray Python-level prints go to stderr, never onto the protocol stream
            try:
                await server.run(read_stream, write_stream, server.create_initialization_options())
            finally:
                sys.stdout = saved
                # Flush anything buffered on the original stdout objects while fd 1 still points at
                # stderr; the SDK restores fd 1 to the wire when this context exits.
                for stream in (saved, sys.__stdout__):
                    try:
                        if stream is not None:
                            stream.flush()
                    except (OSError, ValueError):
                        pass

    roots = ", ".join(f"{r.alias}={r.path} (profiles: {', '.join(sorted(r.profiles)) or 'none'})" for r in service.roots.values())
    _log(f"serving over stdio (invariantguard {ENGINE_VERSION}, mcp {sdk_version()}); roots: {roots}; timeout {service.max_timeout_s:g} s")
    anyio.run(main)
    _log("stdin closed; exiting")
    return 0


__all__ = ["build_server", "serve_stdio", "MCPUnavailable", "sdk_version", "SERVER_NAME"]
