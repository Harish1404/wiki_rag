"""
Composition root: the one place where the app's ports are connected to the
infra adapters (Clerk + MongoDB + MCP connectors).

Everything else depends on `auth_service` and never imports Clerk or Motor
directly. To swap a provider, change only the wiring below.
"""

import logging

from app.auth.service import AuthService
from app.connectors.catalog import CATALOG, GITHUB
from app.connectors.service import ConnectorService
from app.core.config import settings
from app.core.encrypt import FernetTokenCipher
from infra.auth.clerk_identity_provider import ClerkIdentityProvider
from infra.auth.clerk_token_verifier import ClerkTokenVerifier
from infra.db.connection_repository import MongoConnectionRepository
from infra.db.oauth_transaction_repository import MongoOAuthTransactionRepository
from infra.db.user_repository import MongoUserRepository
from infra.mcp.client import LangChainMCPToolProvider
from infra.oauth.github import GitHubOAuthProvider


logger = logging.getLogger(__name__)

user_repository = MongoUserRepository()

auth_service = AuthService(
    token_verifier=ClerkTokenVerifier(
        secret_key=settings.CLERK_SECRET_KEY,
        issuer=settings.CLERK_ISSUER,
        authorized_parties=settings.CLERK_AUTHORIZED_PARTIES,
        jwt_key=settings.CLERK_JWT_KEY,
    ),
    identity_provider=ClerkIdentityProvider(secret_key=settings.CLERK_SECRET_KEY),
    user_repository=user_repository,
)


# ── MCP connectors ───────────────────────────────────────────
# One OAuth provider per catalog entry that needs one. A new connector that
# uses plain OAuth 2.0 adds a line here; nothing else in the app changes.
connection_repository = MongoConnectionRepository()
oauth_transaction_repository = MongoOAuthTransactionRepository()

oauth_providers = {}
if GITHUB.enabled:
    oauth_providers[GITHUB.key] = GitHubOAuthProvider(
        client_id=settings.GITHUB_CLIENT_ID,
        client_secret=settings.GITHUB_CLIENT_SECRET,
        redirect_uri=settings.GITHUB_CALLBACK_URL,
        scopes=GITHUB.scopes,
        timeout=settings.MCP_TIMEOUT_SECONDS,
    )

token_cipher = None
if settings.CONNECTOR_ENCRYPTION_KEYS:
    token_cipher = FernetTokenCipher(settings.CONNECTOR_ENCRYPTION_KEYS)
else:
    logger.warning("CONNECTOR_ENCRYPTION_KEYS is not set; connectors are disabled.")

connector_service = ConnectorService(
    catalog=CATALOG,
    oauth_providers=oauth_providers,
    connections=connection_repository,
    transactions=oauth_transaction_repository,
    cipher=token_cipher,
    mcp=LangChainMCPToolProvider(timeout_seconds=settings.MCP_TIMEOUT_SECONDS),
    allowed_return_origins=settings.CONNECTOR_RETURN_ORIGINS,
)
