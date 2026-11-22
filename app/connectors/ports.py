"""
Ports (interfaces) the connector service depends on. Adapters live in infra/:

    ConnectionRepository        -> infra/db/connection_repository.py
    OAuthTransactionRepository  -> infra/db/oauth_transaction_repository.py
    TokenCipher                 -> app/core/encrypt.py
    OAuthProvider               -> infra/oauth/github.py  (one per provider)
    MCPToolProvider             -> infra/mcp/client.py

Tests swap any of these for simple fakes.
"""

from datetime import datetime
from typing import Protocol

from langchain_core.tools import BaseTool

from app.connectors.models import (
    Connection,
    ConnectionStatus,
    ConnectorDefinition,
    OAuthTransaction,
    RemoteAccount,
    TokenBundle,
)


class TokenCipher(Protocol):
    def encrypt(self, plaintext: str) -> bytes: ...
    def decrypt(self, ciphertext: bytes) -> str: ...


class ConnectionRepository(Protocol):
    async def ensure_indexes(self) -> None: ...

    async def list_for_user(self, user_id: str) -> list[Connection]: ...

    async def get(self, user_id: str, provider_key: str) -> Connection | None:
        """Always filtered by owner: one user can never load another user's connection."""
        ...

    async def upsert_connected(
        self,
        user_id: str,
        definition: ConnectorDefinition,
        account: RemoteAccount,
        token_ciphertext: bytes,
        scopes: list[str],
        expires_at: datetime | None,
    ) -> Connection: ...

    async def update_token(
        self,
        user_id: str,
        provider_key: str,
        previous_ciphertext: bytes,
        token_ciphertext: bytes,
        expires_at: datetime | None,
    ) -> bool:
        """Compare-and-swap: only writes if the stored token is still `previous_ciphertext`."""
        ...

    async def set_status(
        self, user_id: str, provider_key: str, status: ConnectionStatus, error_code: str | None
    ) -> None: ...

    async def delete(self, user_id: str, provider_key: str) -> bool: ...


class OAuthTransactionRepository(Protocol):
    async def ensure_indexes(self) -> None: ...

    async def create(
        self,
        state_hash: str,
        user_id: str,
        provider_key: str,
        return_origin: str,
        pkce_verifier_ciphertext: bytes,
        expires_at: datetime,
    ) -> OAuthTransaction: ...

    async def consume(self, state_hash: str, provider_key: str, now: datetime) -> OAuthTransaction | None:
        """Atomically mark an unexpired, unused transaction as used and return it (else None)."""
        ...


class OAuthProvider(Protocol):
    def authorization_url(self, state: str, code_challenge: str) -> str: ...

    async def exchange_code(self, code: str, code_verifier: str) -> TokenBundle: ...

    async def refresh(self, refresh_token: str) -> TokenBundle: ...

    async def fetch_account(self, access_token: str) -> RemoteAccount: ...

    async def revoke(self, access_token: str) -> None:
        """Best effort: errors are logged, not raised."""
        ...


class MCPToolProvider(Protocol):
    async def list_tools(self, definition: ConnectorDefinition, access_token: str) -> list[BaseTool]:
        """Connect, initialize, discover tools. Raises MCPAuthError on 401/403."""
        ...
