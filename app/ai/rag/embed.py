from google import genai
from google.genai import types

from app.ai.batchs.batch_processing import BatchProcess
from app.ai.constants import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    GEMINI_FREE_TIER_SETTINGS,
)
from app.core.config import settings


class GeminiEmbeddings:
    """Talks to the Gemini embedding API. Owns the model name and vector size."""

    def __init__(
        self,
        model_name: str = EMBEDDING_MODEL,
        dimensions: int = EMBEDDING_DIMENSIONS
    ):
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)

        self.model_name = model_name
        self.dimensions = dimensions

    async def embed(self, contents: str | list[str], task_type: str) -> list[list[float]]:

        response = await self.client.aio.models.embed_content(
            model=self.model_name,
            contents=contents,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=self.dimensions
            )
        )

        return [item.values for item in response.embeddings]


class Embedding:
    """Embeds documents (batched + rate limited) and queries (single call)."""

    def __init__(
        self,
        provider: GeminiEmbeddings | None = None,
        batch_processor: BatchProcess | None = None
    ):

        self.provider = provider or GeminiEmbeddings()
        self.batch_processor = batch_processor or BatchProcess(**GEMINI_FREE_TIER_SETTINGS)

    @property
    def model_name(self) -> str:
        return self.provider.model_name

    @property
    def dimensions(self) -> int:
        return self.provider.dimensions

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        return await self.provider.embed(batch, "RETRIEVAL_DOCUMENT")

    async def embed_chunks(self, chunks: list[str]) -> list[list[float]]:
        return await self.batch_processor.run(chunks, self._embed_batch)

    async def embed_query(self, prompt: str) -> list[float]:
        embeddings = await self.provider.embed(prompt, "RETRIEVAL_QUERY")

        return embeddings[0]


embedding = Embedding()
