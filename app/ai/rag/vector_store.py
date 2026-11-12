import json
import logging

from datetime import datetime, timezone

from bson import ObjectId
from pymongo.operations import SearchIndexModel

from app.ai.constants import (
    DEFAULT_N_RESULTS,
    NUM_CANDIDATES_MULTIPLIER,
    VECTOR_FILTER_FIELD,
    VECTOR_INDEX_NAME,
    VECTOR_SIMILARITY,
    IngestionStatus,
)
from app.ai.rag.embed import embedding
from app.db.mongo import (
    get_chunks_collection,
    get_sources_collection
)


logger = logging.getLogger(__name__)


class VectorStore:

    def __init__(self, dimensions: int):
        # Injected from the embedder, so the index and the vectors can never disagree
        self.dimensions = dimensions

    # -------------------------------
    # Index Management
    # -------------------------------

    def _index_fields(self) -> list[dict]:
        """The vector index definition this code expects."""

        return [
            {
                "type": "vector",
                "path": "embedding",
                "numDimensions": self.dimensions,
                "similarity": VECTOR_SIMILARITY
            },
            {
                "type": "filter",
                "path": VECTOR_FILTER_FIELD
            }
        ]

    @staticmethod
    def _normalize(fields: list[dict]) -> list[str]:
        """Order-independent form of an index field list, for comparison."""

        return sorted(json.dumps(field, sort_keys=True) for field in fields)

    async def ensure_vector_index(self):
        """
        Makes sure the Atlas vector index exists AND matches the expected definition.
        Creates it if missing, updates it in place if it drifted (no extra index slot used).
        Raises on failure so the app does not start with a broken index.
        """

        collection = get_chunks_collection()
        expected_fields = self._index_fields()

        try:
            cursor = collection.list_search_indexes(VECTOR_INDEX_NAME)
            existing = await cursor.to_list(length=1)

            if not existing:
                await collection.create_search_index(
                    model=SearchIndexModel(
                        definition={"fields": expected_fields},
                        name=VECTOR_INDEX_NAME,
                        type="vectorSearch"
                    )
                )
                logger.info("Created vector index '%s'.", VECTOR_INDEX_NAME)
                return

            current_fields = (existing[0].get("latestDefinition") or {}).get("fields", [])

            if self._normalize(current_fields) == self._normalize(expected_fields):
                logger.info("Vector index '%s' is up to date.", VECTOR_INDEX_NAME)
                return

            await collection.update_search_index(
                VECTOR_INDEX_NAME,
                {"fields": expected_fields}
            )
            logger.warning(
                "Vector index '%s' definition was out of date; updated it. Old fields: %s",
                VECTOR_INDEX_NAME,
                current_fields
            )

        except Exception:
            logger.exception("Failed to ensure Atlas Vector Search index.")
            raise

    # -------------------------------
    # Document Lookups
    # -------------------------------

    async def get_document_by_hash(
        self,
        file_hash: str
    ) -> dict | None:

        collection = get_sources_collection()

        doc = await collection.find_one({
            "file_hash": file_hash
        })

        if not doc:
            return None

        doc["source_id"] = str(doc.pop("_id"))

        return doc

    # -------------------------------
    # Document Writes
    # -------------------------------

    async def insert_source(self, document_record: dict) -> str:
        """Inserts the source document record. Returns its id as a string."""

        result = await get_sources_collection().insert_one(document_record)

        return str(result.inserted_id)


    async def insert_chunks(
        self,
        source_id: str,
        filename: str,
        chunks: list[dict],
        embeddings: list[list[float]]
    ) -> int:
        """Inserts chunks with their embeddings, linked to `source_id`. Returns the count."""

        if len(chunks) != len(embeddings):
            raise ValueError(
                "Number of chunks and embeddings must match."
            )

        for emb in embeddings:
            if len(emb) != self.dimensions:
                raise ValueError(
                    f"Expected {self.dimensions} dimensions, "
                    f"received {len(emb)}."
                )

        source_oid = ObjectId(source_id)

        chunk_docs = [
            {
                "source_id": source_oid,
                "filename": filename,
                "chunk_index": chunk["chunk_index"],
                "page_number": chunk["page_number"],
                "text": chunk["text"],
                "embedding": emb
            }
            for chunk, emb in zip(
                chunks,
                embeddings,
                strict=True
            )
        ]

        if chunk_docs:
            await get_chunks_collection().insert_many(chunk_docs)

        logger.info(
            "Inserted %d chunks for '%s'.",
            len(chunk_docs),
            filename
        )

        return len(chunk_docs)


    async def mark_completed(self, source_id: str) -> str:
        """Flags a source as fully ingested. Returns the completion timestamp."""

        ingested_at = datetime.now(timezone.utc).isoformat()

        await get_sources_collection().update_one(
            {"_id": ObjectId(source_id)},
            {"$set": {
                "ingestion_status": IngestionStatus.COMPLETED.value,
                "ingested_at": ingested_at
            }}
        )

        return ingested_at


    async def delete_document(self, source_id: str) -> None:
        """Removes a source and all of its chunks (used to clean up partial ingestions)."""

        source_oid = ObjectId(source_id)

        chunks_result = await get_chunks_collection().delete_many({"source_id": source_oid})
        await get_sources_collection().delete_one({"_id": source_oid})

        logger.info(
            "Deleted source %s and %d chunks.",
            source_id,
            chunks_result.deleted_count
        )

    # -------------------------------
    # Vector Retrieval
    # -------------------------------

    async def retrieve(
        self,
        query_embedding: list[float],
        n_results: int = DEFAULT_N_RESULTS,
        document_id: str | None = None
    ) -> list[dict]:

        if n_results <= 0:
            raise ValueError(
                "n_results must be greater than zero."
            )

        if len(query_embedding) != self.dimensions:
            raise ValueError(
                "Query embedding dimensions do not match "
                "the vector index."
            )

        collection = get_chunks_collection()

        vector_search = {
            "index": VECTOR_INDEX_NAME,
            "path": "embedding",
            "queryVector": query_embedding,
            "numCandidates": n_results * NUM_CANDIDATES_MULTIPLIER,
            "limit": n_results
        }

        # Optional source document filter
        if document_id:

            if not ObjectId.is_valid(document_id):
                raise ValueError("Invalid document ID.")

            vector_search["filter"] = {
                VECTOR_FILTER_FIELD: ObjectId(document_id)
            }

        pipeline = [
            {
                "$vectorSearch": vector_search
            },
            {
                "$project": {
                    "_id": 1,
                    "text": 1,
                    "source_id": 1,
                    "filename": 1,
                    "chunk_index": 1,
                    "page_number": 1,
                    "score": {
                        "$meta": "vectorSearchScore"
                    }
                }
            }
        ]

        results = []

        async for doc in collection.aggregate(pipeline):

            results.append({
                "id": str(doc["_id"]),
                "document": doc["text"],
                "metadata": {
                    "source_id": str(doc["source_id"]),
                    "filename": doc["filename"],
                    "chunk_index": doc["chunk_index"],
                    "page_number": doc["page_number"]
                },
                "score": doc["score"]
            })

        return results


vector_store = VectorStore(dimensions=embedding.dimensions)
