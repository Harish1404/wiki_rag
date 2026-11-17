"""
Auth business logic: turn an incoming request into an application User.

Flow (first-authenticated-request provisioning):
    1. Verify the session token            -> AuthPrincipal (trusted clerk_user_id)
    2. Look up the user in MongoDB
    3. Not found? Fetch profile from Clerk once and create the user
    4. Found? Refresh last_login (at most every LAST_LOGIN_UPDATE_INTERVAL)
    5. Deactivated users are refused (403)
"""

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from app.auth.errors import PermissionDeniedError
from app.auth.models import User
from app.auth.ports import IdentityProvider, TokenVerifier, UserRepository


logger = logging.getLogger(__name__)

# Writing last_login on every chat request would add a database write per message.
# Updating it at most every 15 minutes still tells us when the user was last active.
LAST_LOGIN_UPDATE_INTERVAL = timedelta(minutes=15)


class AuthService:

    def __init__(
        self,
        token_verifier: TokenVerifier,
        identity_provider: IdentityProvider,
        user_repository: UserRepository,
    ):
        self.token_verifier = token_verifier
        self.identity_provider = identity_provider
        self.user_repository = user_repository

    async def get_current_user(self, headers: Mapping[str, str]) -> User:
        principal = await self.token_verifier.verify(headers)

        user = await self.user_repository.get_by_clerk_id(principal.clerk_user_id)

        if user is None:
            # Only a brand-new user costs a call to Clerk's API.
            profile = await self.identity_provider.get_profile(principal.clerk_user_id)
            user = await self.user_repository.create_if_absent(principal.clerk_user_id, profile)
            logger.info("Provisioned new application user id=%s", user.id)

        elif self._last_login_is_stale(user):
            await self.user_repository.touch_last_login(user.clerk_user_id)

        if not user.is_active:
            raise PermissionDeniedError("This account has been deactivated.")

        return user

    @staticmethod
    def _last_login_is_stale(user: User) -> bool:
        if user.last_login is None:
            return True

        last_login = user.last_login
        # MongoDB returns naive datetimes (stored as UTC), so mark them as UTC before comparing.
        if last_login.tzinfo is None:
            last_login = last_login.replace(tzinfo=timezone.utc)

        return datetime.now(timezone.utc) - last_login > LAST_LOGIN_UPDATE_INTERVAL
