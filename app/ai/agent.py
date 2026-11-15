"""
Answering agents, built with LangChain's create_agent — one per route.

    rag_agent     RAG route     document context in the prompt, no tools
    tool_agent    TOOL route    weather_tool + inr_to_usd
    direct_agent  DIRECT route  general knowledge, no tools

Fallback and retry come from LangChain middleware, not hand-written loops:

    ModelFallbackMiddleware   (outer)  primary_llm -> llm2 -> llm3
      └─ ModelRetryMiddleware (inner)  retries EACH model with exponential
                                       backoff + jitter before falling back
    ToolRetryMiddleware                (tool agent only) retries failing tools

The first middleware in the list wraps the ones after it, so every model
is retried before the agent moves on to the next one.
"""

from collections.abc import Sequence

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    ToolRetryMiddleware,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from app.ai.constants import (
    CHAT_BACKOFF_FACTOR,
    CHAT_BASE_RETRY_DELAY,
    CHAT_MAX_RETRIES,
    CHAT_MAX_RETRY_DELAY,
    TOOL_MAX_RETRIES,
    Route,
)
from app.ai.models import active_primary_llm, fallback_llms
from app.ai.prompts.gemini_prompt import (
    DIRECT_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    TOOL_SYSTEM_PROMPT,
)
from app.ai.tools.tool import tools as route_tools


def build_agent(
    name: str,
    system_prompt: str,
    primary: BaseChatModel = active_primary_llm,
    fallbacks: Sequence[BaseChatModel] = tuple(fallback_llms),
    tools: Sequence[BaseTool] = (),
    max_retries: int = CHAT_MAX_RETRIES,
    initial_delay: float = CHAT_BASE_RETRY_DELAY,
    max_delay: float = CHAT_MAX_RETRY_DELAY,
):
    middleware = []

    if fallbacks:
        middleware.append(ModelFallbackMiddleware(*fallbacks))

    middleware.append(
        ModelRetryMiddleware(
            max_retries=max_retries,
            # "error" re-raises after the last retry so the fallback can take over.
            # The default "continue" would turn the failure into a normal AI message.
            on_failure="error",
            backoff_factor=CHAT_BACKOFF_FACTOR,
            initial_delay=initial_delay,
            max_delay=max_delay,
            jitter=True,
        )
    )

    if tools:
        middleware.append(
            ToolRetryMiddleware(
                max_retries=TOOL_MAX_RETRIES,
                backoff_factor=CHAT_BACKOFF_FACTOR,
                initial_delay=initial_delay,
                max_delay=max_delay,
                jitter=True,
            )
        )

    return create_agent(
        model=primary,
        tools=list(tools),
        system_prompt=system_prompt,
        middleware=middleware,
        name=name,
    )


# Kept for existing callers/tests: a tool-less agent with the RAG prompt
def build_rag_agent(primary, fallbacks=(), **kwargs):
    return build_agent("rag_agent", SYSTEM_PROMPT, primary=primary, fallbacks=fallbacks, **kwargs)


rag_agent = build_agent("rag_agent", SYSTEM_PROMPT)
tool_agent = build_agent("tool_agent", TOOL_SYSTEM_PROMPT, tools=route_tools)
direct_agent = build_agent("direct_agent", DIRECT_SYSTEM_PROMPT)

agents_by_route = {
    Route.RAG: rag_agent,
    Route.TOOL: tool_agent,
    Route.DIRECT: direct_agent,
}
