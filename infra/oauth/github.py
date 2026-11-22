"""
GitHub OAuth App adapter (OAuthProvider port).

GitHub's remote MCP server does not offer Dynamic Client Registration, so we
use our own OAuth App registered on github.com (GITHUB_CLIENT_ID / GITHUB_SECRET_KEY).

Endpoints (docs.github.com "Authorizing OAuth apps"):
    authorize  https://github.com/login/oauth/authorize      (PKCE S256 supported)
    token      https://github.com/login/oauth/access_token
    revoke     DELETE https://api.github.com/applications/{client_id}/grant  (basic auth)
"""

import logging

import httpx

from app.connectors.models import RemoteAccount
from infra.oauth.oauth2_pkce import OAuth2PKCEClient


logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
GITHUB_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


class GitHubOAuthProvider(OAuth2PKCEClient):
    authorize_endpoint = "https://github.com/login/oauth/authorize"
    token_endpoint = "https://github.com/login/oauth/access_token"

    async def fetch_account(self, access_token: str) -> RemoteAccount:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{GITHUB_API}/user",
                headers={**GITHUB_API_HEADERS, "Authorization": f"Bearer {access_token}"},
            )
        response.raise_for_status()
        user = response.json()
        return RemoteAccount(id=str(user["id"]), login=user["login"])

    async def revoke(self, access_token: str) -> None:
        # Deletes the whole grant, so the token stops working and the app disappears
        # from the user's "Authorized OAuth Apps" list on GitHub.
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(
                "DELETE",
                f"{GITHUB_API}/applications/{self.client_id}/grant",
                auth=(self.client_id, self.client_secret),
                headers=GITHUB_API_HEADERS,
                json={"access_token": access_token},
            )
        if response.status_code not in (204, 404, 422):
            logger.warning("GitHub grant revocation returned HTTP %s", response.status_code)
