from dotenv import load_dotenv
import os

load_dotenv()


def _split_csv(value: str | None) -> list[str]:
    """Turns "a, b,c" into ["a", "b", "c"], ignoring empty entries."""
    if not value:
        return []
    return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]


class Settings:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")

    MONGO_URL = os.getenv("MONGO_URL")
    DB_NAME = os.getenv("DB_NAME")

    FRONTEND_URL = os.getenv("FRONTEND_URL")

    # ── Clerk authentication ──────────────────────────────────
    # Secret key: server-only. Used to fetch Clerk's public keys and user profiles.
    CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY")
    # Optional PEM public key (Clerk Dashboard -> API Keys). Makes verification fully offline.
    CLERK_JWT_KEY = os.getenv("CLERK_JWT_KEY")
    # Required. Your Clerk Frontend API URL, e.g. https://xxx.clerk.accounts.dev
    # (Clerk Dashboard -> API Keys). Must match the token's "iss" claim exactly.
    # Deliberately NOT guessed from other keys: a guessed value once pointed at the
    # wrong Clerk app and rejected every token. The app refuses to start without it.
    CLERK_ISSUER = os.getenv("CLERK_ISSUER")
    # Frontend origins allowed to use tokens (the token's "azp").
    # Defaults to FRONTEND_URL, then to the local dev server, so this check is never switched off.
    CLERK_AUTHORIZED_PARTIES = _split_csv(
        os.getenv("CLERK_AUTHORIZED_PARTIES") or FRONTEND_URL or "http://localhost:3000"
    )

    # ── MCP connectors ────────────────────────────────────────
    # Fernet keys that encrypt users' connector tokens at rest. Comma-separated:
    # the FIRST key encrypts new data, every key can decrypt (so keys can be rotated).
    # Generate one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Without it, connectors are switched off (the rest of the app still runs).
    CONNECTOR_ENCRYPTION_KEYS = _split_csv(os.getenv("CONNECTOR_ENCRYPTION_KEYS"))
    # How long one MCP request (connect, list tools, call a tool) may take.
    MCP_TIMEOUT_SECONDS = float(os.getenv("MCP_TIMEOUT_SECONDS") or 30)
    # Browser origins the OAuth callback may send people back to. Same list as the
    # origins allowed to use Clerk tokens, so the two can never disagree.
    CONNECTOR_RETURN_ORIGINS = CLERK_AUTHORIZED_PARTIES

    # GitHub (remote MCP server + the OAuth App registered on github.com)
    GITHUB_REMOTE_SERVER = os.getenv("GITHUB_REMOTE_SERVER") or "https://api.githubcopilot.com/mcp"
    GITHUB_CALLBACK_URL = os.getenv("GITHUB_CALLBACK_URL")
    GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
    GITHUB_CLIENT_SECRET = os.getenv("GITHUB_SECRET_KEY")
    # Which groups of GitHub tools the server exposes (see GitHub's MCP server docs).
    # "context" holds get_me ("who am I"), which the assistant needs for "my repos".
    GITHUB_MCP_TOOLSETS = os.getenv("GITHUB_MCP_TOOLSETS") or "context,repos,issues,pull_requests,users"


settings = Settings()
