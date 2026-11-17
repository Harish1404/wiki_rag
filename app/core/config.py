from dotenv import load_dotenv
import base64
import binascii
import os

load_dotenv()


def _issuer_from_publishable_key(publishable_key: str | None) -> str | None:
    """
    A Clerk publishable key looks like "pk_test_<base64 of 'xxx.clerk.accounts.dev$'>".
    Decoding it gives the Frontend API host, which is the token issuer ("iss").
    The publishable key is public, so this reveals nothing secret.
    """
    if not publishable_key or not publishable_key.startswith(("pk_test_", "pk_live_")):
        return None

    encoded = publishable_key.split("_", 2)[2]
    try:
        # Clerk strips base64 padding, so add it back before decoding.
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None

    host = decoded.rstrip("$")
    return f"https://{host}" if host else None


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
    # Your Clerk Frontend API URL, e.g. https://xxx.clerk.accounts.dev — must match the token's "iss".
    # If not set, it is worked out from CLERK_PUBLISHABLE_KEY.
    CLERK_ISSUER = os.getenv("CLERK_ISSUER") or _issuer_from_publishable_key(
        os.getenv("CLERK_PUBLISHABLE_KEY")
    )
    # Frontend origins allowed to use tokens (the token's "azp").
    # Defaults to FRONTEND_URL, then to the local dev server, so this check is never switched off.
    CLERK_AUTHORIZED_PARTIES = _split_csv(
        os.getenv("CLERK_AUTHORIZED_PARTIES") or FRONTEND_URL or "http://localhost:3000"
    )


settings = Settings()
