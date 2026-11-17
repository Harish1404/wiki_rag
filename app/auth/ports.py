"""
Ports (interfaces) that the auth business logic depends on.

The real implementations (adapters) live in infra/:
    TokenVerifier    -> infra/auth/clerk_token_verifier.py
    IdentityProvider -> infra/auth/clerk_identity_provider.py
    UserRepository   -> infra/db/user_repository.py

typing.Protocol means "any class with these methods fits", so the service never
imports Clerk or Motor, and tests can pass simple fakes instead.
"""

from collections.abc import Mapping
from typing import Protocol

from app.auth.models import AuthPrincipal, IdentityProfile, User


class TokenVerifier(Protocol):
    async def verify(self, headers: Mapping[str, str]) -> AuthPrincipal:
        """Verify the Bearer token in the headers. Raises AuthenticationError if invalid."""
        ...


class IdentityProvider(Protocol):
    async def get_profile(self, clerk_user_id: str) -> IdentityProfile:
        """Fetch the user's email and name from the identity provider."""
        ...


class UserRepository(Protocol):
    async def get_by_clerk_id(self, clerk_user_id: str) -> User | None:
        ...

    async def create_if_absent(self, clerk_user_id: str, profile: IdentityProfile) -> User:
        """Create the user, or return the existing one. Must be safe to call concurrently."""
        ...

    async def touch_last_login(self, clerk_user_id: str) -> None:
        ...

    async def ensure_indexes(self) -> None:
        ...
