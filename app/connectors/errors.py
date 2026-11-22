"""
Connector errors with stable, non-secret codes.

The codes are safe to show in URLs, API responses and logs. Messages never
contain tokens, authorization codes or raw provider responses.
"""

from enum import Enum


class ConnectorErrorCode(str, Enum):
    UNKNOWN_CONNECTOR = "MCP_UNKNOWN_CONNECTOR"
    CONNECTOR_UNAVAILABLE = "MCP_CONNECTOR_UNAVAILABLE"   # not configured on this server
    NOT_CONNECTED = "MCP_AUTH_REQUIRED"
    OAUTH_DENIED = "MCP_OAUTH_DENIED"
    OAUTH_STATE_INVALID = "MCP_OAUTH_STATE_INVALID"       # unknown, expired or already used
    OAUTH_EXCHANGE_FAILED = "MCP_OAUTH_EXCHANGE_FAILED"
    TOKEN_REFRESH_FAILED = "MCP_TOKEN_REFRESH_FAILED"
    SERVER_UNREACHABLE = "MCP_SERVER_UNREACHABLE"
    TOOL_DISCOVERY_FAILED = "MCP_TOOL_DISCOVERY_FAILED"
    INVALID_RETURN_ORIGIN = "MCP_INVALID_RETURN_ORIGIN"


class ConnectorError(Exception):

    def __init__(self, code: ConnectorErrorCode, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class MCPAuthError(Exception):
    """The MCP server rejected the stored token (401/403): the user must reconnect."""
