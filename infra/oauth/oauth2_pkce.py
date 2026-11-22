"""
Generic OAuth 2.0 authorization-code client with PKCE (S256), for pre-registered apps.

Provider files (e.g. infra/oauth/github.py) give it their endpoints and add
anything provider-specific (account lookup, revocation). A future provider
that speaks plain OAuth 2.0 only needs a new subclass with its URLs.

Nothing here logs tokens, codes or response bodies.
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from app.connectors.models import TokenBundle




class OAuthExchangeError(Exception):
    """The provider refused the code or refresh token. Message holds only the provider's error code."""


class OAuth2PKCEClient:
    authorize_endpoint: str
    token_endpoint: str
    # Character the provider uses between scopes in its token response.
    scope_separator = " "

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scopes: list[str],
        timeout: float = 30,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scopes
        self.timeout = timeout

    def authorization_url(self, state: str, code_challenge: str) -> str:
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "scope": " ".join(self.scopes),
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "response_type": "code",
            }
        )
        return f"{self.authorize_endpoint}?{query}"

    async def exchange_code(self, code: str, code_verifier: str) -> TokenBundle:
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": code_verifier,
            }
        )

    async def refresh(self, refresh_token: str) -> TokenBundle:
        return await self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})

    async def _token_request(self, form: dict[str, str]) -> TokenBundle:
        # The client secret stays on the server and only goes to the provider's token endpoint.
        form = {**form, "client_id": self.client_id, "client_secret": self.client_secret}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(self.token_endpoint, data=form, headers={"Accept": "application/json"})

        try:
            body = response.json()
        except ValueError:
            body = {}

        # Some providers (GitHub) answer 200 with {"error": "..."}; check both.
        if response.status_code >= 400 or "error" in body or "access_token" not in body:
            error_code = str(body.get("error") or f"http_{response.status_code}")[:60]
            raise OAuthExchangeError(error_code)

        expires_at = None
        if body.get("expires_in"):
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(body["expires_in"]))

        raw_scope = body.get("scope") or ""
        scopes = [s.strip() for s in raw_scope.replace(",", " ").split() if s.strip()]

        return TokenBundle(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            token_type=body.get("token_type") or "bearer",
            expires_at=expires_at,
            scopes=scopes,
        )
