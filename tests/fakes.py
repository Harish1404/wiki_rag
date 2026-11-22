"""In-memory fakes for the connector ports. No MongoDB, GitHub or secrets needed."""

import itertools
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from langchain_core.tools import BaseTool, tool

from app.auth.models import User
from app.connectors.errors import MCPAuthError
from app.connectors.models import (
    Connection,
    ConnectionStatus,
    ConnectorDefinition,
    OAuthTransaction,
    RemoteAccount,
    TokenBundle,
)
from app.connectors.service import ConnectorService
from app.core.encrypt import FernetTokenCipher

ORIGIN = "http://localhost:3000"
_ids = itertools.count(1)


def make_user(user_id: str) -> User:
    return User(id=user_id, clerk_user_id=f"clerk_{user_id}", created_at=datetime.now(timezone.utc))


DEFINITION = ConnectorDefinition(
    key="github",
    display_name="GitHub",
    description="test",
    server_url="https://mcp.example.test/mcp",
    scopes=["repo"],
)


class FakeConnections:
    def __init__(self):
        self.docs: dict[tuple[str, str], Connection] = {}

    async def ensure_indexes(self): ...

    async def list_for_user(self, user_id):
        return [c for (uid, _), c in self.docs.items() if uid == user_id]

    async def get(self, user_id, provider_key):
        return self.docs.get((user_id, provider_key))

    async def upsert_connected(self, user_id, definition, account, token_ciphertext, scopes, expires_at):
        now = datetime.now(timezone.utc)
        connection = Connection(
            id=str(next(_ids)), user_id=user_id, provider_key=definition.key, server_url=definition.server_url,
            transport=definition.transport, auth_type=definition.auth_type, status=ConnectionStatus.CONNECTED,
            scopes=scopes, remote_account_id=account.id, remote_account_login=account.login,
            token_ciphertext=token_ciphertext, expires_at=expires_at, created_at=now, updated_at=now,
            last_connected_at=now,
        )
        self.docs[(user_id, definition.key)] = connection
        return connection

    async def update_token(self, user_id, provider_key, previous_ciphertext, token_ciphertext, expires_at):
        current = self.docs.get((user_id, provider_key))
        if current is None or current.token_ciphertext != previous_ciphertext:
            return False
        self.docs[(user_id, provider_key)] = current.model_copy(
            update={"token_ciphertext": token_ciphertext, "expires_at": expires_at}
        )
        return True

    async def set_status(self, user_id, provider_key, status, error_code):
        current = self.docs.get((user_id, provider_key))
        if current:
            self.docs[(user_id, provider_key)] = current.model_copy(
                update={"status": status, "last_error_code": error_code}
            )

    async def delete(self, user_id, provider_key):
        return self.docs.pop((user_id, provider_key), None) is not None


class FakeTransactions:
    def __init__(self):
        self.docs: dict[str, OAuthTransaction] = {}

    async def ensure_indexes(self): ...

    async def create(self, state_hash, user_id, provider_key, return_origin, pkce_verifier_ciphertext, expires_at):
        transaction = OAuthTransaction(
            id=str(next(_ids)), state_hash=state_hash, user_id=user_id, provider_key=provider_key,
            return_origin=return_origin, pkce_verifier_ciphertext=pkce_verifier_ciphertext,
            expires_at=expires_at, created_at=datetime.now(timezone.utc),
        )
        self.docs[state_hash] = transaction
        return transaction

    async def consume(self, state_hash, provider_key, now):
        transaction = self.docs.get(state_hash)
        if transaction is None or transaction.provider_key != provider_key or transaction.expires_at <= now:
            return None
        del self.docs[state_hash]  # single use, like find_one_and_delete
        return transaction


class FakeOAuth:
    def __init__(self, tokens: TokenBundle | None = None):
        self.tokens = tokens or TokenBundle(access_token="gho_SECRET_ACCESS_TOKEN", scopes=["repo"])
        self.last_verifier: str | None = None
        self.last_state: str | None = None
        self.revoked: list[str] = []
        self.refresh_calls = 0

    def authorization_url(self, state, code_challenge):
        self.last_state = state
        return f"https://auth.example.test/authorize?state={state}&code_challenge={code_challenge}"

    async def exchange_code(self, code, code_verifier):
        self.last_verifier = code_verifier
        return self.tokens

    async def refresh(self, refresh_token):
        self.refresh_calls += 1
        return TokenBundle(access_token="gho_REFRESHED", refresh_token="rt_NEW")

    async def fetch_account(self, access_token):
        return RemoteAccount(id="42", login="octocat")

    async def revoke(self, access_token):
        self.revoked.append(access_token)


@tool
def fake_tool(query: str) -> str:
    """A fake MCP tool."""
    return query


class FakeMCP:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.tokens_seen: list[str] = []

    async def list_tools(self, definition, access_token) -> list[BaseTool]:
        self.tokens_seen.append(access_token)
        if self.fail:
            raise self.fail
        return [fake_tool]


def make_service(oauth=None, mcp=None, cipher=True):
    return ConnectorService(
        catalog={"github": DEFINITION},
        oauth_providers={"github": oauth or FakeOAuth()},
        connections=FakeConnections(),
        transactions=FakeTransactions(),
        cipher=FernetTokenCipher([Fernet.generate_key().decode()]) if cipher else None,
        mcp=mcp or FakeMCP(),
        allowed_return_origins=[ORIGIN],
    )


__all__ = ["MCPAuthError"]
