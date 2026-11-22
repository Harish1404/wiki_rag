"""
The connector catalog: every remote MCP server this app can connect to.

To add a new server:
    1. Add a ConnectorDefinition below (URL, scopes, headers).
    2. Add an OAuthProvider for it in infra/oauth/ if it doesn't follow GitHub's flow,
       and register it in app/core/container.py.
The agent, routes, database and frontend need no changes.

Only these catalog servers can be reached. Users cannot enter their own URLs,
which keeps the backend from being used to call arbitrary hosts (SSRF).
"""

from app.core.config import settings
from app.connectors.models import ConnectorDefinition


GITHUB = ConnectorDefinition(
    key="github",
    display_name="GitHub",
    description="Ask about your repositories, issues and pull requests.",
    server_url=settings.GITHUB_REMOTE_SERVER,
    # repo: read private repositories too. GitHub has no read-only "repo" scope, so
    # the read-only header below (plus our own tool filter) is what blocks writes.
    scopes=["repo", "read:org", "read:user"],
    mcp_headers={
        "X-MCP-Readonly": "true",
        "X-MCP-Toolsets": settings.GITHUB_MCP_TOOLSETS,
    },
    read_only=True,
    enabled=bool(
        settings.GITHUB_CLIENT_ID and settings.GITHUB_CLIENT_SECRET and settings.GITHUB_CALLBACK_URL
    ),
)


CATALOG: dict[str, ConnectorDefinition] = {
    GITHUB.key: GITHUB,
}
