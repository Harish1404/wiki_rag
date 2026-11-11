import json
import logging

from collections.abc import AsyncIterator, Callable

from app.ai.constants import (
    CHAT_BASE_RETRY_DELAY,
    CHAT_MAX_RETRIES,
    CHAT_MAX_RETRY_DELAY,
    NO_CONTEXT_MESSAGE,
)
from app.ai.rag.pipeline import rag_pipeline
from app.ai.models import models
from app.ai.prompts.gemini_prompt import RAG_PROMPT_TEMPLATE
from app.utils.retry import retry_with_jitter


logger = logging.getLogger(__name__)


class ChatService:

    def __init__(
        self,
        rag_pipeline,
        model_registry,
        prompt_template: str
    ):

        self.rag_pipeline = rag_pipeline
        self.model_registry = model_registry
        self.prompt_template = prompt_template

    # ----------------------------------
    # Retry Mechanism
    # ----------------------------------

    @staticmethod
    async def _retry(label: str, func, *args):
        """Retries `func(*args)` using the shared jitter helper and chat retry settings."""

        return await retry_with_jitter(
            func,
            *args,
            max_retries=CHAT_MAX_RETRIES,
            base_delay=CHAT_BASE_RETRY_DELAY,
            max_delay=CHAT_MAX_RETRY_DELAY,
            label=label
        )

    # ----------------------------------
    # Open LLM Stream
    # ----------------------------------

    async def _open_stream(
        self,
        model_func: Callable[[str], AsyncIterator[str]],
        prompt: str
    ) -> tuple[str, AsyncIterator[str]]:

        stream = model_func(prompt)

        try:
            first_token = await anext(stream)
        except StopAsyncIteration:
            raise RuntimeError("Model returned an empty response stream.")

        return first_token, stream

    # ----------------------------------
    # SSE Formatting
    # ----------------------------------

    @staticmethod
    def _sse(event: dict) -> str:

        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    # ----------------------------------
    # Main Chat Workflow
    # ----------------------------------

    async def chat(
        self,
        user_prompt: str
    ) -> AsyncIterator[str]:

        # 1. Retrieve relevant document chunks

        try:
            retrieval = await self._retry(
                "Document retrieval",
                self.rag_pipeline.retrieve,
                user_prompt
            )

        except Exception:
            logger.exception("Document retrieval failed.")

            yield self._sse({
                "type": "error",
                "message": "Could not search the documents."
            })
            return

        if not retrieval["results"]:

            yield self._sse({
                "type": "token",
                "content": NO_CONTEXT_MESSAGE
            })

            yield self._sse({"type": "done"})
            return

        # 2. Build context and construct prompt

        context, sources = self.rag_pipeline.build_context(
            retrieval["results"]
        )

        prompt = self.prompt_template.format(
            context=context,
            question=user_prompt
        )

        # 3. Get model fallback chain

        fallback_chain = self.model_registry.get_fallback_chain()

        # 4. Try each model until one successfully starts

        for model_name, model_func in fallback_chain:

            logger.info("Trying LLM provider: %s", model_name)

            try:
                first_token, stream = await self._retry(
                    f"Open {model_name} stream",
                    self._open_stream,
                    model_func,
                    prompt
                )

            except Exception:

                logger.exception(
                    "Provider %s failed. Trying next provider.",
                    model_name
                )

                continue

            # 5. Notify frontend about selected model

            yield self._sse({
                "type": "model",
                "model": model_name
            })

            # 6. Send first token

            yield self._sse({
                "type": "token",
                "content": first_token
            })

            # 7. Stream remaining tokens

            try:

                async for token in stream:

                    yield self._sse({
                        "type": "token",
                        "content": token
                    })

            except Exception:

                logger.exception(
                    "Provider %s failed during streaming.",
                    model_name
                )

                yield self._sse({
                    "type": "error",
                    "message": f"{model_name} failed during streaming."
                })

                return

            # 8. Send sources and completion event

            yield self._sse({
                "type": "sources",
                "sources": sources
            })

            yield self._sse({"type": "done"})

            logger.info(
                "Successfully generated response using %s.",
                model_name
            )

            return

        # 9. All providers failed

        yield self._sse({
            "type": "error",
            "message": (
                "All LLM providers failed. "
                "Please check credentials or network."
            )
        })


# ----------------------------------
# Application-level instance
# ----------------------------------

chat_service = ChatService(
    rag_pipeline=rag_pipeline,
    model_registry=models,
    prompt_template=RAG_PROMPT_TEMPLATE
)

