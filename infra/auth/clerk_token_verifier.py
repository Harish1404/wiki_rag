"""
Clerk adapter for the TokenVerifier port (app/auth/ports.py).

This file and clerk_identity_provider.py are the only places that know about
Clerk. Swapping identity providers means replacing these files, not the app.

What gets checked on every request:
    - "Authorization: Bearer <token>" header is present        (done here)
    - RS256 signature against Clerk's public keys (JWKS)       (Clerk SDK)
    - exp / nbf expiry claims, with small clock-skew leeway    (Clerk SDK)
    - azp is one of our allowed frontend origins               (Clerk SDK)
    - token is a session token, not an M2M / API key           (Clerk SDK)
    - iss is our Clerk instance                                (done here: the SDK skips it)

Clerk's SDK caches the JWKS keys in memory, so there is no Clerk network call
per request. Setting CLERK_JWT_KEY removes even the first fetch.
"""

import logging
from collections.abc import Mapping

from clerk_backend_api.security import authenticate_request_async
from clerk_backend_api.security.types import AuthenticateRequestOptions

from app.auth.errors import AuthenticationError
from app.auth.models import AuthPrincipal


logger = logging.getLogger(__name__)

BEARER_PREFIX = "bearer "


class _BearerOnlyRequest:
    """
    Gives the Clerk SDK only the Authorization header.

    The SDK also falls back to reading a "__session" cookie. Our API is
    Bearer-token only, so we hide cookies from it on purpose.
    """

    def __init__(self, token: str):
        self.headers = {"Authorization": f"Bearer {token}"}


def _extract_bearer_token(headers: Mapping[str, str]) -> str:
    # Starlette headers are case-insensitive, so "authorization" also matches "Authorization".
    authorization = headers.get("authorization")
    if not authorization:
        raise AuthenticationError("Missing Authorization header.")

    if not authorization.lower().startswith(BEARER_PREFIX):
        raise AuthenticationError("Authorization header must use the Bearer scheme.")

    token = authorization[len(BEARER_PREFIX):].strip()
    if not token:
        raise AuthenticationError("Bearer token is empty.")

    return token


class ClerkTokenVerifier:

    def __init__(
        self,
        secret_key: str | None,
        issuer: str | None,
        authorized_parties: list[str],
        jwt_key: str | None = None,
    ):
        if not secret_key and not jwt_key:
            raise RuntimeError("Set CLERK_SECRET_KEY (or CLERK_JWT_KEY) to verify Clerk tokens.")
        if not issuer:
            raise RuntimeError("Set CLERK_ISSUER to your Clerk Frontend API URL.")

        # A trailing slash would make the issuer comparison fail for no real reason.
        self.issuer = issuer.rstrip("/")
        self.options = AuthenticateRequestOptions(
            secret_key=secret_key,
            jwt_key=jwt_key,
            authorized_parties=authorized_parties or None,
            accepts_token=["session_token"],
        )

    async def verify(self, headers: Mapping[str, str]) -> AuthPrincipal:
        token = _extract_bearer_token(headers)

        state = await authenticate_request_async(_BearerOnlyRequest(token), self.options)

        if not state.is_signed_in or state.payload is None:
            # Log the reason only, never the token itself.
            logger.info("Rejected session token: %s", state.message)
            raise AuthenticationError("Invalid or expired session token.")

        payload = state.payload

        token_issuer = str(payload.get("iss", "")).rstrip("/")
        if token_issuer != self.issuer:
            logger.warning("Rejected session token from an unexpected issuer.")
            raise AuthenticationError("Invalid or expired session token.")

        clerk_user_id = payload.get("sub")
        if not isinstance(clerk_user_id, str) or not clerk_user_id:
            raise AuthenticationError("Session token has no user.")

        return AuthPrincipal(clerk_user_id=clerk_user_id, session_id=payload.get("sid"))
