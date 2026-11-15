"""
Gemini embedding provider.

This file is the ONLY place that knows about the Google GenAI SDK
for generating embeddings. If you switch to OpenAI embeddings,
HuggingFace, or a local model, you replace THIS file and update
the re-export in app/ai/rag/embed.py.

How it works:
    1. GeminiEmbeddings — low-level class that calls the Gemini API
    2. Embedding — high-level service that adds batching + rate limiting
    3. `embedding` — the singleton used by the rest of the app
"""

from google import genai
from google.genai import types

from app.ai.batchs.batch_processing import BatchProcess
from app.ai.constants import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    GEMINI_FREE_TIER_SETTINGS,
)
from app.core.config import settings


# ──────────────────────────────────────────────────────────────
# Low-level: talks to the Gemini embedding API
# ──────────────────────────────────────────────────────────────

class GeminiEmbeddings:
    """
    Wraps the Google GenAI embedding API.
    Owns the model name and the number of dimensions each vector has.
    """

    def __init__(
        self,
        model_name: str = EMBEDDING_MODEL,
        dimensions: int = EMBEDDING_DIMENSIONS
    ):
        # Create the Google GenAI client using the API key from .env
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)

        self.model_name = model_name    # e.g. "gemini-embedding-001"
        self.dimensions = dimensions    # e.g. 3072

    async def embed(
        self,
        contents: str | list[str],
        task_type: str
    ) -> list[list[float]]:
        """
        Call the Gemini embedding API.

        Args:
            contents:  A single string or a list of strings to embed.
            task_type: "RETRIEVAL_DOCUMENT" when storing chunks,
                       "RETRIEVAL_QUERY" when searching.

        Returns:
            A list of embedding vectors (one per input string).
        """

        response = await self.client.aio.models.embed_content(
            model=self.model_name,
            contents=contents,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=self.dimensions
            )
        )

        return [item.values for item in response.embeddings]


# ──────────────────────────────────────────────────────────────
# High-level: adds batching and rate limiting on top
# ──────────────────────────────────────────────────────────────

class Embedding:
    """
    High-level embedding service used by the RAG pipeline.

    - embed_chunks(): batch-embeds many document texts with rate limiting
    - embed_query():  embeds a single search query
    """

    def __init__(
        self,
        provider: GeminiEmbeddings | None = None,
        batch_processor: BatchProcess | None = None
    ):
        # Use defaults if nothing is passed in
        self.provider = provider or GeminiEmbeddings()
        self.batch_processor = batch_processor or BatchProcess(
            **GEMINI_FREE_TIER_SETTINGS
        )

    @property
    def model_name(self) -> str:
        """The name of the embedding model (e.g. 'gemini-embedding-001')."""
        return self.provider.model_name

    @property
    def dimensions(self) -> int:
        """How many numbers each embedding vector has (e.g. 3072)."""
        return self.provider.dimensions

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        """Embed one batch of texts (called by the batch processor)."""
        return await self.provider.embed(batch, "RETRIEVAL_DOCUMENT")

    async def embed_chunks(self, chunks: list[str]) -> list[list[float]]:
        """
        Embed many document chunks with automatic batching and rate limiting.
        This is used during document ingestion.
        """
        return await self.batch_processor.run(chunks, self._embed_batch)

    async def embed_query(self, prompt: str) -> list[float]:
        """
        Embed a single search query and return its vector.
        This is used when a user asks a question.
        """
        embeddings = await self.provider.embed(prompt, "RETRIEVAL_QUERY")
        return embeddings[0]


# ──────────────────────────────────────────────────────────────
# Application-level singleton
# ──────────────────────────────────────────────────────────────
# Created once when the module loads. The rest of the app
# imports this `embedding` object.

embedding = Embedding()
