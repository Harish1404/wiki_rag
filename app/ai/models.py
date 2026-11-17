"""
Bridge file: the real LLM providers live in infra/llm/providers.py.
This file re-exports everything so existing imports still work.

Example:
    from app.ai.models import active_primary_llm   # still works!
    # It just fetches the object from infra/llm/providers.py now.
"""

from infra.llm.providers import (          # noqa: F401 — re-exported
    build_groq_llm,
    build_gemini_llm,
    build_mistral_llm,
    primary_llm,
    llm2,
    llm3,
    active_primary_llm,
    fallback_llms,
    build_router_llms,
)
