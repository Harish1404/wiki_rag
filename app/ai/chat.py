"""
Chat service: route the question, then stream the matching handler's answer as SSE.

    router.route(q) -> RAG | TOOL | DIRECT
      RAG    : retrieve (with_retry) -> RAG_CHAT_PROMPT -> rag_agent      -> sources
      TOOL   : tool_agent (weather_tool, inr_to_usd)                      -> tool events
      DIRECT : direct_agent (general knowledge)
      GITHUB : per-request agent with the user's own GitHub MCP tools      -> tool events

Uses LangChain built-ins only:
  - structured output + with_retry/with_fallbacks -> routing (app/ai/router.py)
  - Runnable.with_retry        -> retrieval retry (exponential backoff + jitter)
  - ChatPromptTemplate         -> question + context -> agent input messages
  - create_agent + middleware  -> model fallback + retry, tool retry (app/ai/agent.py)
  - astream_events(v2)         -> tokens, tool calls, and which model answered

SSE events:
  route -> [RAG: retrieval_start -> retrieval_end] -> [TOOL: tool_start -> tool_end]...
        -> model -> token... -> [RAG: sources] -> done
  (or error at any point)
"""

import json
import logging

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableLambda

from app.ai.agent import agents_by_route, build_agent
from app.ai.constants import (
    GITHUB_NOT_CONNECTED_MESSAGE,
    GITHUB_UNAVAILABLE_MESSAGE,
    NO_CONTEXT_MESSAGE,
    RETRIEVAL_MAX_ATTEMPTS,
    SSE_TOOL_OUTPUT_LIMIT,
    Route,
)
from app.ai.prompts.gemini_prompt import GITHUB_ACCOUNT_PROMPT, GITHUB_SYSTEM_PROMPT, RAG_CHAT_PROMPT
from app.ai.rag.pipeline import rag_pipeline
from app.ai.router import QueryRouter, query_router
from app.auth.models import User
from app.connectors.errors import ConnectorError
from app.connectors.service import ConnectorSession
from app.core.container import connector_service


logger = logging.getLogger(__name__)

ALL_PROVIDERS_FAILED = "All LLM providers failed. Please check credentials or network."

# (user, connector key) -> that user's tools + account name, or None when not connected.
# Injected, so the chat service never touches the database, tokens or OAuth.
ConnectorTools = Callable[[User, str], Awaitable[ConnectorSession | None]]


class ChatService:

    def __init__(
        self,
        rag_pipeline,
        router: QueryRouter,
        agents: Mapping[Route, Runnable],
        rag_prompt: ChatPromptTemplate,
        connector_tools: ConnectorTools | None = None,
    ):

        self.rag_pipeline = rag_pipeline
        self.router = router
        self.agents = agents
        self.rag_prompt = rag_prompt
        self.connector_tools = connector_tools

        # LangChain's built-in retry around the document search
        self.retriever: Runnable = RunnableLambda(
            rag_pipeline.retrieve,
            name="document_retrieval"
        ).with_retry(
            stop_after_attempt=RETRIEVAL_MAX_ATTEMPTS,
            wait_exponential_jitter=True
        )

        # Route -> handler. A new route is a new entry here, not a new if/elif.
        self._handlers: dict[Route, Callable[[str, User | None], AsyncIterator[str]]] = {
            Route.RAG: self._handle_rag,
            Route.TOOL: self._handle_tool,
            Route.DIRECT: self._handle_direct,
            Route.GITHUB: self._handle_github,
        }

    # ----------------------------------
    # SSE helpers
    # ----------------------------------

    @staticmethod
    def _sse(event: dict) -> str:

        return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"

    @staticmethod
    def _model_label(metadata: dict) -> str:
        """e.g. 'groq/openai/gpt-oss-20b', from LangChain's tracing metadata."""

        provider = metadata.get("ls_provider") or "unknown"
        model = metadata.get("ls_model_name") or "unknown"

        return f"{provider}/{model}"

    @staticmethod
    def _tool_output_text(output) -> str:
        """ToolMessage / str / other -> short text for the frontend."""

        text = getattr(output, "content", output)
        text = text if isinstance(text, str) else str(text)

        if len(text) > SSE_TOOL_OUTPUT_LIMIT:
            text = text[:SSE_TOOL_OUTPUT_LIMIT] + "…"

        return text

    # ----------------------------------
    # Main entry point
    # ----------------------------------

    async def chat(
        self,
        user_prompt: str,
        user: User | None = None,
    ) -> AsyncIterator[str]:

        decision = await self.router.route(user_prompt)

        yield self._sse({
            "type": "route",
            "route": decision.route.value,
            "reason": decision.reason
        })

        async for event in self._handlers[decision.route](user_prompt, user):
            yield event

    # ----------------------------------
    # Route handlers
    # ----------------------------------

    async def _handle_rag(self, user_prompt: str, user: User | None = None) -> AsyncIterator[str]:

        # 1. Retrieve relevant document chunks

        yield self._sse({"type": "retrieval_start", "query": user_prompt})

        try:
            retrieval = await self.retriever.ainvoke(user_prompt)

        except Exception:
            logger.exception("Document retrieval failed.")

            yield self._sse({
                "type": "error",
                "message": "Could not search the documents."
            })
            return

        yield self._sse({
            "type": "retrieval_end",
            "results": retrieval["total_results"]
        })

        if not retrieval["results"]:

            yield self._sse({"type": "token", "content": NO_CONTEXT_MESSAGE})
            yield self._sse({"type": "done"})
            return

        # 2. Build context and the agent's input messages from the prompt template

        context, sources = self.rag_pipeline.build_context(retrieval["results"])

        prompt_value = await self.rag_prompt.ainvoke({
            "context": context,
            "question": user_prompt
        })

        # 3. Stream the answer, then sources

        state = {}

        async for event in self._stream_agent(
            self.agents[Route.RAG],
            prompt_value.to_messages(),
            state
        ):
            yield event

        if state.get("ok"):
            yield self._sse({"type": "sources", "sources": sources})
            yield self._sse({"type": "done"})

    async def _handle_tool(self, user_prompt: str, user: User | None = None) -> AsyncIterator[str]:

        async for event in self._answer(Route.TOOL, user_prompt):
            yield event

    async def _handle_direct(self, user_prompt: str, user: User | None = None) -> AsyncIterator[str]:

        async for event in self._answer(Route.DIRECT, user_prompt):
            yield event

    async def _handle_github(self, user_prompt: str, user: User | None = None) -> AsyncIterator[str]:
        """Answers with the signed-in user's OWN GitHub tools; the agent is built per request."""

        session = None
        if user is not None and self.connector_tools is not None:
            try:
                session = await self.connector_tools(user, "github")
            except Exception as error:
                # ConnectorError carries a safe code; anything else, log only its class.
                reason = error.code.value if isinstance(error, ConnectorError) else type(error).__name__
                logger.warning("GitHub tools unavailable: %s", reason)
                yield self._sse({"type": "token", "content": GITHUB_UNAVAILABLE_MESSAGE})
                yield self._sse({"type": "done"})
                return

        if session is None or not session.tools:
            yield self._sse({"type": "token", "content": GITHUB_NOT_CONNECTED_MESSAGE})
            yield self._sse({"type": "done"})
            return

        # Tell the model whose account this is, so "my repos" needs no follow-up question.
        # The login is public profile data, never a credential.
        system_prompt = GITHUB_SYSTEM_PROMPT
        if session.account_login:
            system_prompt += GITHUB_ACCOUNT_PROMPT.format(login=session.account_login)

        # A fresh agent per request: its tools carry only this user's token.
        agent = build_agent("github_agent", system_prompt, tools=session.tools)

        state = {}
        async for event in self._stream_agent(agent, [HumanMessage(content=user_prompt)], state):
            yield event

        if state.get("ok"):
            yield self._sse({"type": "done"})

    async def _answer(self, route: Route, user_prompt: str) -> AsyncIterator[str]:
        """Shared flow for routes whose input is just the question."""

        state = {}

        async for event in self._stream_agent(
            self.agents[route],
            [HumanMessage(content=user_prompt)],
            state
        ):
            yield event

        if state.get("ok"):
            yield self._sse({"type": "done"})

    # ----------------------------------
    # Agent streaming (shared by all routes)
    # ----------------------------------

    async def _stream_agent(
        self,
        agent: Runnable,
        messages: list[BaseMessage],
        state: dict
    ) -> AsyncIterator[str]:
        """
        Streams an agent run as SSE: model / token / tool_start / tool_end.
        Sets state["ok"] = True when an answer was streamed; on failure it
        yields an error event and leaves state["ok"] unset.
        """

        answering_run = None

        try:

            async for event in agent.astream_events(
                {"messages": messages},
                version="v2"
            ):

                kind = event["event"]

                if kind == "on_tool_start":

                    yield self._sse({
                        "type": "tool_start",
                        "tool": event["name"],
                        "input": event["data"].get("input")
                    })

                elif kind == "on_tool_end":

                    yield self._sse({
                        "type": "tool_end",
                        "tool": event["name"],
                        "output": self._tool_output_text(event["data"].get("output"))
                    })

                elif kind == "on_chat_model_stream":

                    text = event["data"]["chunk"].text

                    if not text:
                        continue  # tool-call chunks, reasoning-only or empty chunks

                    # Announce the model on its first text token. A new run id means a
                    # different call (fallback, or the answer after a tool call).
                    if event["run_id"] != answering_run:

                        answering_run = event["run_id"]
                        model = self._model_label(event.get("metadata", {}))

                        logger.info("Answering with %s.", model)

                        yield self._sse({"type": "model", "model": model})

                    yield self._sse({"type": "token", "content": text})

        except Exception:

            logger.exception("All LLM providers failed.")

            yield self._sse({"type": "error", "message": ALL_PROVIDERS_FAILED})
            return

        if answering_run is None:

            yield self._sse({
                "type": "error",
                "message": "The model returned an empty answer."
            })
            return

        state["ok"] = True


# ----------------------------------
# Application-level instance
# ----------------------------------

chat_service = ChatService(
    rag_pipeline=rag_pipeline,
    router=query_router,
    agents=agents_by_route,
    rag_prompt=RAG_CHAT_PROMPT,
    connector_tools=connector_service.get_tools,
)
