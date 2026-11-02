from google import genai
from google.genai import types
from app.ai.batchs.batch_processing import BatchProcess, GEMINI_FREE_TIER_SETTINGS
from app.core.config import settings


class Embedding:
    def __init__(self, model_name: str = "gemini-embedding-001"):
        self.client = genai.Client(api_key = settings.GEMINI_API_KEY)
        self.model_name = model_name
        self.dimensions = 3072
        self.batch_processor = BatchProcess(**GEMINI_FREE_TIER_SETTINGS)

    async def _embed(self, contents: str | list[str], task_type: str) -> list[list[float]]:
        response = await self.client.aio.models.embed_content(
            model = self.model_name,
            contents = contents,
            config = types.EmbedContentConfig(task_type = task_type),
        )
        return [item.values for item in response.embeddings]

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        return await self._embed(batch, "RETRIEVAL_DOCUMENT")

    async def embed_chunks(self, chunks: list[str]) -> list[list[float]]:
        return await self.batch_processor.run(chunks, self._embed_batch)

    async def embed_query(self, prompt: str) -> list[float]:
        # A single call, so it doesn't go through the batch processor
        embeddings = await self._embed(prompt, "RETRIEVAL_QUERY")
        return embeddings[0]


embedding = Embedding()
