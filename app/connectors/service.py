"""
Connector business logic: connect, OAuth callback, status, disconnect, tools for chat.

    start_connect(user, key, origin)
        -> random state + PKCE verifier, stored as a short-lived single-use transaction
        -> returns GitHub's authorization URL (browser goes there)

    complete_callback(key, code, state, error)
        -> consume the transaction (bound to the user who clicked Connect)
        -> exchange code (+ PKCE verifier) for a token
        -> look up the remote account, then PROVE the MCP server works (initialize + list tools)
        -> only then save the connection as "connected", with the token encrypted

    get_tools(user, key)
        -> the user's own tools only; refresh the token if it is about to expire
        -> on 401 from the MCP server: mark "needs_reauth"

Tokens never leave this layer except as the Bearer header the MCP client sends.
"""

import asyncio
import base64
import hashlib
import logging
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from langchain_core.tools import BaseTool

from app.auth.models import User
from app.connectors.errors import ConnectorError, ConnectorErrorCode, MCPAuthError
from app.connectors.models import (
    Connection,
    ConnectionStatus,
    ConnectorDefinition,
    ConnectorView,
    TokenBundle,
)
from app.connectors.ports import (
    ConnectionRepository,
    MCPToolProvider,
    OAuthProvider,
    OAuthTransactionRepository,
    TokenCipher,
)


logger = logging.getLogger(__name__)

TRANSACTION_TTL = timedelta(minutes=10)
# Refresh a token this long before it expires, so a chat never starts with a dying token.
REFRESH_MARGIN = timedelta(minutes=2)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    # MongoDB returns naive datetimes (stored as UTC).
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def hash_state(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def pkce_pair() -> tuple[str, str]:
    """Returns (verifier, S256 challenge) as defined in RFC 7636."""
    verifier = secrets.token_urlsafe(64)  # 86 chars, within the 43-128 limit
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def normalize_origin(value: str | None) -> str | None:
    """'http://localhost:3000/anything' -> 'http://localhost:3000'. None if not an http(s) origin."""
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password:
        return None
    return f"{parts.scheme}://{parts.netloc}"


@dataclass
class ConnectorSession:
    """What the chat agent gets: the user's tools and their (non-secret) account name."""

    tools: list[BaseTool]
    account_login: str | None = None


class CallbackResult:
    """Where to send the browser after the OAuth callback. Contains no secrets."""

    def __init__(self, return_origin: str | None, provider_key: str, result: str, code: str | None = None):
        self.return_origin = return_origin
        self.provider_key = provider_key
        self.result = result          # "connected" | "denied" | "error"
        self.code = code


class ConnectorService:

    def __init__(
        self,
        catalog: Mapping[str, ConnectorDefinition],
        oauth_providers: Mapping[str, OAuthProvider],
        connections: ConnectionRepository,
        transactions: OAuthTransactionRepository,
        cipher: TokenCipher | None,
        mcp: MCPToolProvider,
        allowed_return_origins: list[str],
    ):
        self.catalog = catalog
        self.oauth_providers = oauth_providers
        self.connections = connections
        self.transactions = transactions
        self.cipher = cipher
        self.mcp = mcp
        # Ordered, de-duplicated: the first one is the fallback when a callback has no valid state.
        self._ordered_origins = list(
            dict.fromkeys(o for o in (normalize_origin(x) for x in allowed_return_origins) if o)
        )
        self.allowed_return_origins = set(self._ordered_origins)
        self._refresh_locks: dict[tuple[str, str], asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _definition(self, key: str, *, require_available: bool = True) -> ConnectorDefinition:
        definition = self.catalog.get(key)
        if definition is None:
            raise ConnectorError(ConnectorErrorCode.UNKNOWN_CONNECTOR, "Unknown connector.")
        if require_available and not self._is_available(definition):
            raise ConnectorError(
                ConnectorErrorCode.CONNECTOR_UNAVAILABLE, f"{definition.display_name} is not set up on this server."
            )
        return definition

    def _is_available(self, definition: ConnectorDefinition) -> bool:
        return definition.enabled and self.cipher is not None and definition.key in self.oauth_providers

    def _require_cipher(self) -> TokenCipher:
        if self.cipher is None:
            raise ConnectorError(ConnectorErrorCode.CONNECTOR_UNAVAILABLE, "Connectors are not set up on this server.")
        return self.cipher

    def _encrypt_tokens(self, tokens: TokenBundle) -> bytes:
        return self._require_cipher().encrypt(tokens.model_dump_json())

    def _decrypt_tokens(self, ciphertext: bytes) -> TokenBundle:
        return TokenBundle.model_validate_json(self._require_cipher().decrypt(ciphertext))

    # ------------------------------------------------------------------
    # Catalog + status
    # ------------------------------------------------------------------

    async def list_for_user(self, user: User) -> list[ConnectorView]:
        saved = {connection.provider_key: connection for connection in await self.connections.list_for_user(user.id)}
        views = []
        for definition in self.catalog.values():
            connection = saved.get(definition.key)
            views.append(
                ConnectorView(
                    key=definition.key,
                    display_name=definition.display_name,
                    description=definition.description,
                    available=self._is_available(definition),
                    status=connection.status.value if connection else "not_connected",
                    account_login=connection.remote_account_login if connection else None,
                    scopes=connection.scopes if connection else [],
                    read_only=definition.read_only,
                    connected_at=connection.last_connected_at if connection else None,
                )
            )
        return views

    # ------------------------------------------------------------------
    # Connect: step 1 (signed-in user clicks Connect)
    # ------------------------------------------------------------------

    async def start_connect(self, user: User, key: str, request_origin: str | None) -> str:
        definition = self._definition(key)
        cipher = self._require_cipher()

        return_origin = normalize_origin(request_origin)
        if return_origin not in self.allowed_return_origins:
            raise ConnectorError(ConnectorErrorCode.INVALID_RETURN_ORIGIN, "This site is not allowed to start a connection.")

        state = secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()

        await self.transactions.create(
            state_hash=hash_state(state),
            user_id=user.id,
            provider_key=definition.key,
            return_origin=return_origin,
            pkce_verifier_ciphertext=cipher.encrypt(verifier),
            expires_at=_now() + TRANSACTION_TTL,
        )
        logger.info("Started %s connection for user id=%s", definition.key, user.id)
        return self.oauth_providers[definition.key].authorization_url(state, challenge)

    # ------------------------------------------------------------------
    # Connect: step 2 (provider redirects the browser back to us)
    # ------------------------------------------------------------------

    async def complete_callback(
        self, key: str, code: str | None, state: str | None, error: str | None
    ) -> CallbackResult:
        # Without a valid transaction we don't know who this is or where to send them,
        # so the route falls back to the first allowed origin.
        if not state:
            return CallbackResult(None, key, "error", ConnectorErrorCode.OAUTH_STATE_INVALID.value)

        transaction = await self.transactions.consume(hash_state(state), key, _now())
        if transaction is None:
            logger.warning("Rejected %s OAuth callback: unknown, expired or reused state", key)
            return CallbackResult(None, key, "error", ConnectorErrorCode.OAUTH_STATE_INVALID.value)

        origin = transaction.return_origin
        user_id = transaction.user_id

        if error:
            # e.g. "access_denied" when the user clicks Cancel on GitHub.
            logger.info("User id=%s did not authorize %s (%s)", user_id, key, error[:40])
            code_value = ConnectorErrorCode.OAUTH_DENIED if error == "access_denied" else ConnectorErrorCode.OAUTH_EXCHANGE_FAILED
            return CallbackResult(origin, key, "denied" if error == "access_denied" else "error", code_value.value)

        if not code:
            return CallbackResult(origin, key, "error", ConnectorErrorCode.OAUTH_EXCHANGE_FAILED.value)

        try:
            definition = self._definition(key)
            provider = self.oauth_providers[key]
            verifier = self._require_cipher().decrypt(transaction.pkce_verifier_ciphertext)

            tokens = await provider.exchange_code(code, verifier)
            account = await provider.fetch_account(tokens.access_token)

            # Never report "connected" just because OAuth worked: the MCP server must answer too.
            tools = await self.mcp.list_tools(definition, tokens.access_token)
            logger.info("Verified %s MCP server for user id=%s: %d tools", key, user_id, len(tools))

            await self.connections.upsert_connected(
                user_id=user_id,
                definition=definition,
                account=account,
                token_ciphertext=self._encrypt_tokens(tokens),
                scopes=tokens.scopes or definition.scopes,
                expires_at=tokens.expires_at,
            )
        except ConnectorError as failure:
            logger.warning("Connecting %s failed for user id=%s: %s", key, user_id, failure.code.value)
            return CallbackResult(origin, key, "error", failure.code.value)
        except MCPAuthError:
            logger.warning("%s MCP server rejected a fresh token for user id=%s", key, user_id)
            return CallbackResult(origin, key, "error", ConnectorErrorCode.TOOL_DISCOVERY_FAILED.value)
        except Exception as failure:
            # Class name only: provider errors can echo request data.
            logger.error("Connecting %s failed for user id=%s: %s", key, user_id, type(failure).__name__)
            return CallbackResult(origin, key, "error", ConnectorErrorCode.SERVER_UNREACHABLE.value)

        logger.info("Connected %s for user id=%s as %s", key, user_id, account.login)
        return CallbackResult(origin, key, "connected")

    def fallback_origin(self) -> str | None:
        return self._ordered_origins[0] if self._ordered_origins else None

    # ------------------------------------------------------------------
    # Disconnect
    # ------------------------------------------------------------------

    async def disconnect(self, user: User, key: str) -> bool:
        self._definition(key, require_available=False)
        connection = await self.connections.get(user.id, key)
        if connection is None:
            return False

        provider = self.oauth_providers.get(key)
        if provider is not None and self.cipher is not None:
            try:
                tokens = self._decrypt_tokens(connection.token_ciphertext)
                await provider.revoke(tokens.access_token)
            except Exception as failure:
                # The local record is deleted anyway, so the agent can't use it.
                logger.warning("Could not revoke %s token for user id=%s: %s", key, user.id, type(failure).__name__)

        deleted = await self.connections.delete(user.id, key)
        logger.info("Disconnected %s for user id=%s", key, user.id)
        return deleted

    # ------------------------------------------------------------------
    # Tools for the chat agent
    # ------------------------------------------------------------------

    async def get_tools(self, user: User, key: str) -> ConnectorSession | None:
        """The user's tools for one connector, or None when they haven't connected it (or must reconnect)."""
        definition = self._definition(key)
        connection = await self.connections.get(user.id, key)
        if connection is None or connection.status != ConnectionStatus.CONNECTED:
            return None

        tokens = await self._fresh_tokens(user, definition, connection)
        if tokens is None:
            return None

        try:
            tools = await self.mcp.list_tools(definition, tokens.access_token)
            logger.info("%s tools for user id=%s: %s", key, user.id, ", ".join(t.name for t in tools))
            return ConnectorSession(tools=tools, account_login=connection.remote_account_login)
        except MCPAuthError:
            await self.connections.set_status(
                user.id, key, ConnectionStatus.NEEDS_REAUTH, ConnectorErrorCode.NOT_CONNECTED.value
            )
            logger.warning("%s token rejected for user id=%s; marked needs_reauth", key, user.id)
            return None

    async def _fresh_tokens(
        self, user: User, definition: ConnectorDefinition, connection: Connection
    ) -> TokenBundle | None:
        tokens = self._decrypt_tokens(connection.token_ciphertext)
        expires_at = _as_utc(tokens.expires_at)
        if expires_at is None or expires_at - REFRESH_MARGIN > _now():
            return tokens  # GitHub OAuth App tokens usually never expire

        if not tokens.refresh_token:
            await self.connections.set_status(
                user.id, definition.key, ConnectionStatus.NEEDS_REAUTH, ConnectorErrorCode.TOKEN_REFRESH_FAILED.value
            )
            return None

        # One refresh at a time per connection in this process: a rotated refresh token
        # can only be used once, so a second concurrent refresh would fail.
        lock = self._refresh_locks.setdefault((user.id, definition.key), asyncio.Lock())
        async with lock:
            latest = await self.connections.get(user.id, definition.key)
            if latest is not None and latest.token_ciphertext != connection.token_ciphertext:
                return self._decrypt_tokens(latest.token_ciphertext)  # someone else just refreshed
            return await self._refresh(user, definition, connection, tokens)

    async def _refresh(
        self, user: User, definition: ConnectorDefinition, connection: Connection, tokens: TokenBundle
    ) -> TokenBundle | None:
        try:
            refreshed = await self.oauth_providers[definition.key].refresh(tokens.refresh_token or "")
        except Exception as failure:
            logger.warning("Refreshing %s token failed for user id=%s: %s", definition.key, user.id, type(failure).__name__)
            await self.connections.set_status(
                user.id, definition.key, ConnectionStatus.NEEDS_REAUTH, ConnectorErrorCode.TOKEN_REFRESH_FAILED.value
            )
            return None

        # Providers rotate refresh tokens: keep the old one only if no new one came back.
        if not refreshed.refresh_token:
            refreshed.refresh_token = tokens.refresh_token

        # Compare-and-swap: if two chats refreshed at once, the first write wins and the
        # second reloads the winner's token instead of overwriting it.
        swapped = await self.connections.update_token(
            user.id, definition.key, connection.token_ciphertext, self._encrypt_tokens(refreshed), refreshed.expires_at
        )
        if not swapped:
            latest = await self.connections.get(user.id, definition.key)
            return self._decrypt_tokens(latest.token_ciphertext) if latest else None
        return refreshed
