"""
MCP client adapter (MCPToolProvider port), built on LangChain's MCPAdapter
(`langchain[mcp]`, which uses FastMCP underneath).

For every request we build a NEW client carrying only that user's token. There
is no shared, process-wide client, so one user's credentials can never be used
for another user's tools.

What this adds on top of the plain adapter:
    - Streamable HTTP transport with "Authorization: Bearer <token>" + connector headers
    - timeouts for connecting and for each call
    - app-side read-only policy: tools the server marks as non-read-only are dropped
    - tool names namespaced by connector ("github__search_repositories")
    - tool output capped, so one huge file can't flood the model's context
    - 401/403 from the server turned into MCPAuthError (the user must reconnect)
"""

import logging
import warnings
from typing import Any

import httpx2
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport
from langchain_core._api import LangChainBetaWarning
from langchain_core.tools import BaseTool, StructuredTool

from app.connectors.errors import ConnectorError, ConnectorErrorCode, MCPAuthError
from app.connectors.models import ConnectorDefinition

with warnings.catch_warnings():
    # langchain.mcp is marked beta; we pin the installed version in requirements.
    warnings.simplefilter("ignore", LangChainBetaWarning)
    from langchain.mcp import MCPAdapter


logger = logging.getLogger(__name__)

# Characters of tool output passed back to the model per tool call.
TOOL_OUTPUT_LIMIT = 20_000
TOOL_NAME_SEPARATOR = "__"


def _http_status(error: BaseException) -> int | None:
    """Finds an HTTP status code anywhere in an exception, its causes or exception groups."""
    seen: set[int] = set()
    stack: list[BaseException] = [error]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        status = getattr(getattr(current, "response", None), "status_code", None)
        if isinstance(status, int):
            return status
        if isinstance(current, BaseExceptionGroup):
            stack.extend(current.exceptions)
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                stack.append(linked)
    return None


def _is_read_only(tool: BaseTool) -> bool | None:
    """The server's own readOnlyHint, if it sent one."""
    annotations = ((tool.metadata or {}).get("mcp", {}).get("tool", {}).get("annotations", {}))
    hint = annotations.get("read_only_hint")
    return hint if isinstance(hint, bool) else None


def _cap_content(content: Any) -> Any:
    """Truncates text in a tool result (string or list of content blocks)."""
    if isinstance(content, str):
        return content if len(content) <= TOOL_OUTPUT_LIMIT else content[:TOOL_OUTPUT_LIMIT] + "\n…(truncated)"
    if isinstance(content, list):
        remaining = TOOL_OUTPUT_LIMIT
        capped = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                text = block["text"]
                if remaining <= 0:
                    break
                if len(text) > remaining:
                    block = {**block, "text": text[:remaining] + "\n…(truncated)"}
                remaining -= len(block["text"])
            capped.append(block)
        return capped
    return content


def _wrap(tool: BaseTool, prefix: str) -> BaseTool:
    """Same tool, namespaced name and capped output. The MCP call itself is unchanged."""
    original = tool.coroutine  # StructuredTool from langchain.mcp; calls the MCP tool by its real name

    async def call(**arguments: Any):
        result = await original(**arguments)
        if isinstance(result, tuple) and len(result) == 2:
            content, artifact = result
            return _cap_content(content), artifact
        return _cap_content(result)

    return StructuredTool(
        name=f"{prefix}{TOOL_NAME_SEPARATOR}{tool.name}",
        description=tool.description,
        args_schema=tool.args_schema,
        coroutine=call,
        response_format=tool.response_format,
        metadata=tool.metadata,
        handle_tool_error=tool.handle_tool_error,
    )


class _StatusRecorder:
    """
    Remembers a 401/403 seen on the wire. The MCP SDK turns HTTP errors into a
    generic MCPError without the status code, so this is how we tell "token
    rejected, reconnect" apart from "server down".
    """

    def __init__(self):
        self.auth_failure: int | None = None

    async def _on_response(self, response: httpx2.Response) -> None:
        if response.status_code in (401, 403):
            self.auth_failure = response.status_code

    def client_factory(self, **kwargs: Any) -> httpx2.AsyncClient:
        # Same defaults as the MCP SDK's own factory (30s, long read for SSE streams).
        kwargs.setdefault("timeout", httpx2.Timeout(30.0, read=300.0))
        return httpx2.AsyncClient(**kwargs, event_hooks={"response": [self._on_response]})


class LangChainMCPToolProvider:

    def __init__(self, timeout_seconds: float = 30):
        self.timeout_seconds = timeout_seconds

    def _client(self, definition: ConnectorDefinition, access_token: str, statuses: "_StatusRecorder") -> Client:
        if definition.transport != "streamable_http":
            raise ConnectorError(ConnectorErrorCode.TOOL_DISCOVERY_FAILED, "Unsupported MCP transport.")
        transport = StreamableHttpTransport(
            definition.server_url,
            headers=dict(definition.mcp_headers),
            auth=access_token,  # sent as "Authorization: Bearer <token>"
            httpx_client_factory=statuses.client_factory,
        )
        return Client(
            transport,
            name=f"wiki-rag-{definition.key}",
            timeout=self.timeout_seconds,
            init_timeout=self.timeout_seconds,
        )

    async def list_tools(self, definition: ConnectorDefinition, access_token: str) -> list[BaseTool]:
        statuses = _StatusRecorder()
        try:
            adapter = MCPAdapter(self._client(definition, access_token, statuses))
            remote_tools = await adapter.list_tools()
        except ConnectorError:
            raise
        except Exception as error:
            status = statuses.auth_failure or _http_status(error)
            if status in (401, 403):
                raise MCPAuthError() from error
            # Class name + status only: errors can contain request details.
            logger.warning(
                "MCP tool discovery failed for %s: %s (HTTP %s)", definition.key, type(error).__name__, status
            )
            code = ConnectorErrorCode.SERVER_UNREACHABLE if status is None else ConnectorErrorCode.TOOL_DISCOVERY_FAILED
            raise ConnectorError(code, f"Could not reach {definition.display_name}.") from error

        tools = []
        for tool in remote_tools:
            if definition.read_only and _is_read_only(tool) is False:
                continue  # the server says this tool writes; our policy is read-only
            tools.append(_wrap(tool, definition.key))

        logger.info("Discovered %d %s tools (%d kept)", len(remote_tools), definition.key, len(tools))
        return tools
