"""
LangChain chat model providers.

    primary_llm -> Groq    (GROQ_CHAT_MODEL)
    llm2        -> Gemini  (GEMINI_CHAT_MODEL)
    llm3        -> Mistral (MISTRAL_CHAT_MODEL)

Each model is built with max_retries=0 so the agent's ModelRetryMiddleware
is the ONLY retry layer (no hidden SDK retries stacking on top of it).
A provider whose API key is missing is skipped with a warning.
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


def _missing_key(provider: str, env_var: str) -> None:
    logger.warning("%s is not configured (%s missing in .env); skipping it.", provider, env_var)
    return None


def build_groq_llm() -> BaseChatModel | None:
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
    if not settings.GEMINI_API_KEY:
        return _missing_key("Gemini", "GEMINI_API_KEY")

    # No temperature: Gemini 3.x models use fixed sampling and warn if one is passed
    return ChatGoogleGenerativeAI(
        model=GEMINI_CHAT_MODEL,
        api_key=settings.GEMINI_API_KEY,
        timeout=LLM_TIMEOUT,
        max_retries=0,
    )


def build_mistral_llm() -> BaseChatModel | None:
    if not settings.MISTRAL_API_KEY:
        return _missing_key("Mistral", "MISTRAL_API_KEY")

    return ChatMistralAI(
        model=MISTRAL_CHAT_MODEL,
        api_key=settings.MISTRAL_API_KEY,
        temperature=LLM_TEMPERATURE,
        timeout=LLM_TIMEOUT,
        max_retries=0,
    )


# ─────────────────────────────────────────────────────────
# Application-level models (in fallback order)
# ─────────────────────────────────────────────────────────

primary_llm = build_groq_llm()
llm2 = build_gemini_llm()
llm3 = build_mistral_llm()

_available = [m for m in (primary_llm, llm2, llm3) if m is not None]

if not _available:
    raise RuntimeError("No LLM provider is configured. Add at least one API key to .env.")

# If the primary is missing, the next available model is promoted
active_primary_llm: BaseChatModel = _available[0]
fallback_llms: list[BaseChatModel] = _available[1:]


# ─────────────────────────────────────────────────────────
# Router models (separate instances: deterministic settings)
# ─────────────────────────────────────────────────────────

def build_router_llms() -> list[BaseChatModel]:
    """Router order: Mistral -> Groq -> Gemini, temperature 0, providers without keys skipped."""

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
