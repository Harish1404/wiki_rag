"""
Query router: an LLM reads the question first and picks ONE route.

    Mistral -> Groq -> Gemini      (LangChain .with_retry() + .with_fallbacks())
      └─ all failed -> DEFAULT_ROUTE (RAG)

The router returns structured output (RouteDecision), so the route is always
one of the Route enum values — never free text that has to be parsed.
"""

import logging

from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from app.ai.constants import DEFAULT_ROUTE, ROUTER_MAX_ATTEMPTS, Route
from app.ai.models import build_router_llms
from app.ai.prompts.routes import ROUTER_PROMPT


logger = logging.getLogger(__name__)


class RouteDecision(BaseModel):
    """The route chosen for a user message."""

    route: Route = Field(description="RAG for Virat Kohli questions, TOOL for weather or INR to USD, DIRECT for everything else.")
    reason: str = Field(description="One short sentence explaining the choice.")


class QueryRouter:

    def __init__(
        self,
        llms: Sequence[BaseChatModel],
        prompt: ChatPromptTemplate = ROUTER_PROMPT,
        default_route: Route = DEFAULT_ROUTE,
        max_attempts: int = ROUTER_MAX_ATTEMPTS
    ):

        self.default_route = default_route

        chains: list[Runnable] = [
            (prompt | llm.with_structured_output(RouteDecision)).with_retry(
                stop_after_attempt=max_attempts,
                wait_exponential_jitter=True
            )
            for llm in llms
        ]

        if not chains:
            logger.warning("No router model configured; every query goes to %s.", default_route)

        self.chain: Runnable | None = (
            chains[0].with_fallbacks(chains[1:]) if chains else None
        )

    async def route(self, question: str) -> RouteDecision:

        if self.chain is None:
            return RouteDecision(route=self.default_route, reason="No router model configured.")

        try:
            decision = await self.chain.ainvoke({"question": question})

            if not isinstance(decision, RouteDecision):
                raise ValueError(f"Router returned unexpected output: {decision!r}")

            logger.info("Routed to %s: %s", decision.route, decision.reason)
            return decision

        except Exception:
            logger.exception("All router models failed; defaulting to %s.", self.default_route)

            return RouteDecision(
                route=self.default_route,
                reason="Router unavailable; used the default route."
            )


query_router = QueryRouter(build_router_llms())
