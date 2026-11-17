"""
Composition root: the one place where the app's ports are connected to the
infra adapters (Clerk + MongoDB).

Everything else depends on `auth_service` and never imports Clerk or Motor
directly. To swap a provider, change only the wiring below.
"""

from app.auth.service import AuthService
from app.core.config import settings
from infra.auth.clerk_identity_provider import ClerkIdentityProvider
from infra.auth.clerk_token_verifier import ClerkTokenVerifier
from infra.db.user_repository import MongoUserRepository


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
