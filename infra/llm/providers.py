"""
LLM (Large Language Model) providers.

This file is the ONLY place that knows about the specific LLM SDKs:
    - langchain_groq   (ChatGroq)
    - langchain_google_genai (ChatGoogleGenerativeAI)
    - langchain_mistralai (ChatMistralAI)

If you want to add a new LLM (e.g. OpenAI, Anthropic) or remove one,
edit THIS file only — the rest of the app just uses `active_primary_llm`
and `fallback_llms`.

How it works:
    1. Each build_*_llm() function creates a chat model if the API key exists
    2. Models are ordered in fallback priority: Groq -> Gemini -> Mistral
    3. The first available model becomes `active_primary_llm`
    4. The rest become `fallback_llms` (tried if the primary fails)
    5. Router models are separate instances with temperature=0 for deterministic routing
"""

import logging

from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_mistralai import ChatMistralAI

from app.ai.constants import (
    GEMINI_CHAT_MODEL,
    GROQ_CHAT_MODEL,
    LLM_TEMPERATURE,
    LLM_TIMEOUT,
    MISTRAL_CHAT_MODEL,
    ROUTER_TEMPERATURE,
)
from app.core.config import settings


logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Helper: log a warning when an API key is missing
# ──────────────────────────────────────────────────────────────

def _missing_key(provider: str, env_var: str) -> None:
    """Log that a provider is skipped because its API key is not set."""
    logger.warning(
        "%s is not configured (%s missing in .env); skipping it.",
        provider, env_var
    )
    return None


# ──────────────────────────────────────────────────────────────
# Builder functions — one per LLM provider
# ──────────────────────────────────────────────────────────────
# Each returns a LangChain chat model or None if the key is missing.
# max_retries=0 so the agent's ModelRetryMiddleware is the ONLY retry
# layer (no hidden SDK retries stacking on top of it).

def build_groq_llm() -> BaseChatModel | None:
    """Build the Groq chat model (primary LLM)."""
    if not settings.GROQ_API_KEY:
        return _missing_key("Groq", "GROQ_API_KEY")

    return ChatGroq(
        model=GROQ_CHAT_MODEL,
        api_key=settings.GROQ_API_KEY,
        temperature=LLM_TEMPERATURE,
        timeout=LLM_TIMEOUT,
        max_retries=0,
    )


def build_gemini_llm() -> BaseChatModel | None:
    """Build the Gemini chat model (first fallback)."""
    if not settings.GEMINI_API_KEY:
        return _missing_key("Gemini", "GEMINI_API_KEY")

    # No temperature: Gemini 3.x models use fixed sampling
    # and warn if one is passed
    return ChatGoogleGenerativeAI(
        model=GEMINI_CHAT_MODEL,
        api_key=settings.GEMINI_API_KEY,
        timeout=LLM_TIMEOUT,
        max_retries=0,
    )


def build_mistral_llm() -> BaseChatModel | None:
    """Build the Mistral chat model (second fallback)."""
    if not settings.MISTRAL_API_KEY:
        return _missing_key("Mistral", "MISTRAL_API_KEY")

    return ChatMistralAI(
        model=MISTRAL_CHAT_MODEL,
        api_key=settings.MISTRAL_API_KEY,
        temperature=LLM_TEMPERATURE,
        timeout=LLM_TIMEOUT,
        max_retries=0,
    )


# ──────────────────────────────────────────────────────────────
# Application-level models (in fallback order)
# ──────────────────────────────────────────────────────────────
# These are created once when the module loads.
# The app uses `active_primary_llm` for the main model
# and `fallback_llms` for backup models.

primary_llm = build_groq_llm()       # Try Groq first
llm2 = build_gemini_llm()            # Then Gemini
llm3 = build_mistral_llm()           # Then Mistral

# Filter out any that returned None (missing API key)
_available = [m for m in (primary_llm, llm2, llm3) if m is not None]

if not _available:
    raise RuntimeError(
        "No LLM provider is configured. Add at least one API key to .env."
    )

# If the primary is missing, the next available model is promoted
active_primary_llm: BaseChatModel = _available[0]
fallback_llms: list[BaseChatModel] = _available[1:]

logger.info(
    "LLM providers ready: primary=%s, fallbacks=%d",
    type(active_primary_llm).__name__,
    len(fallback_llms),
)


# ──────────────────────────────────────────────────────────────
# Router models (separate instances with deterministic settings)
# ──────────────────────────────────────────────────────────────
# The router uses temperature=0 so routing decisions are consistent.
# Router order: Mistral -> Groq -> Gemini (Mistral is cheapest for classification).

def build_router_llms() -> list[BaseChatModel]:
    """
    Build a list of LLMs specifically for the query router.
    Router order: Mistral -> Groq -> Gemini, temperature 0.
    Providers without API keys are skipped.
    """

    router_llms: list[BaseChatModel] = []

    if settings.MISTRAL_API_KEY:
        router_llms.append(ChatMistralAI(
            model=MISTRAL_CHAT_MODEL,
            api_key=settings.MISTRAL_API_KEY,
            temperature=ROUTER_TEMPERATURE,
            timeout=LLM_TIMEOUT,
            max_retries=0,
        ))

    if settings.GROQ_API_KEY:
        router_llms.append(ChatGroq(
            model=GROQ_CHAT_MODEL,
            api_key=settings.GROQ_API_KEY,
            temperature=ROUTER_TEMPERATURE,
            timeout=LLM_TIMEOUT,
            max_retries=0,
        ))

    if settings.GEMINI_API_KEY:
        router_llms.append(ChatGoogleGenerativeAI(
            model=GEMINI_CHAT_MODEL,
            api_key=settings.GEMINI_API_KEY,
            timeout=LLM_TIMEOUT,
            max_retries=0,
        ))

    return router_llms
