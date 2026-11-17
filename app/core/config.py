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


settings = Settings()
