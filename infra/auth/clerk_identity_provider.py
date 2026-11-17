"""
Clerk adapter for the IdentityProvider port (app/auth/ports.py).

Session tokens do not include the user's email or name by default, so on a
user's FIRST request we ask Clerk's Backend API for them once. After that the
profile is read from MongoDB.
"""

import logging

from clerk_backend_api import Clerk

from app.auth.models import IdentityProfile


logger = logging.getLogger(__name__)


class ClerkIdentityProvider:

    def __init__(self, secret_key: str | None):
        self.secret_key = secret_key

    async def get_profile(self, clerk_user_id: str) -> IdentityProfile:
        if not self.secret_key:
            # Without a secret key we cannot call Clerk's API; the user is still created.
            logger.warning("CLERK_SECRET_KEY is not set; creating user without profile details.")
            return IdentityProfile()

        async with Clerk(bearer_auth=self.secret_key) as clerk:
            clerk_user = await clerk.users.get_async(user_id=clerk_user_id)

        primary_email = next(
            (
                address.email_address
                for address in clerk_user.email_addresses
                if address.id == clerk_user.primary_email_address_id
            ),
            None,
        )

        return IdentityProfile(
            email=primary_email,
            first_name=clerk_user.first_name,
            last_name=clerk_user.last_name,
        )
