"""
Connector domain models (plain pydantic: no FastAPI, Motor or MCP imports).

    ConnectorDefinition  one entry in the catalog (what we CAN connect to)
    Connection           one user's saved link to a connector (what they DID connect)
    OAuthTransaction     a pending "Connect" click, waiting for the OAuth callback
    TokenBundle          the OAuth tokens; only ever stored encrypted
"""

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


AuthType = Literal["oauth2"]
Transport = Literal["streamable_http"]


class ConnectionStatus(str, Enum):
    CONNECTED = "connected"
    NEEDS_REAUTH = "needs_reauth"   # token revoked/expired: the user must connect again
    ERROR = "error"


class ConnectorDefinition(BaseModel):
    """Static description of a remote MCP server we support. Lives in app/connectors/catalog.py."""

    key: str                                  # "github" — used in URLs and tool names
    display_name: str
    description: str
    server_url: str
    transport: Transport = "streamable_http"
    auth_type: AuthType = "oauth2"
    scopes: list[str] = Field(default_factory=list)
    # Extra HTTP headers sent to the MCP server (e.g. GitHub's read-only switch).
    mcp_headers: dict[str, str] = Field(default_factory=dict)
    # App-side policy on top of the server's own: refuse tools marked as writing.
    read_only: bool = True
    # False when its settings (client id, secret, ...) are missing: shown as unavailable.
    enabled: bool = True


class TokenBundle(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_at: Optional[datetime] = None
    scopes: list[str] = Field(default_factory=list)


class RemoteAccount(BaseModel):
    """Who the user is on the remote service (e.g. their GitHub login)."""

    id: str
    login: str


class Connection(BaseModel):
    """Mirrors one document in the "mcp_connections" collection."""

    id: str
    user_id: str                     # application users._id — never taken from the browser
    provider_key: str
    server_url: str
    transport: Transport
    auth_type: AuthType
    status: ConnectionStatus
    scopes: list[str] = Field(default_factory=list)
    remote_account_id: Optional[str] = None
    remote_account_login: Optional[str] = None
    token_ciphertext: bytes          # encrypted TokenBundle JSON
    expires_at: Optional[datetime] = None
    last_error_code: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    last_connected_at: Optional[datetime] = None


class OAuthTransaction(BaseModel):
    """Mirrors one document in the "oauth_transactions" collection. Single use, short-lived."""

    id: str
    state_hash: str                  # sha256 of the random state; the raw state is never stored
    user_id: str
    provider_key: str
    return_origin: str               # allowlisted frontend origin to send the browser back to
    pkce_verifier_ciphertext: bytes
    expires_at: datetime
    consumed_at: Optional[datetime] = None
    created_at: datetime


class ConnectorView(BaseModel):
    """What the API returns for one connector. Contains no secrets by construction."""

    key: str
    display_name: str
    description: str
    available: bool
    status: Literal["not_connected", "connected", "needs_reauth", "error"]
    account_login: Optional[str] = None
    scopes: list[str] = Field(default_factory=list)
    read_only: bool = True
    connected_at: Optional[datetime] = None
