"""
MongoDB Atlas Vector Search store.

This file is the ONLY place that knows about:
    - BSON ObjectId
    - MongoDB aggregation pipelines ($vectorSearch)
    - Atlas Search Index management (SearchIndexModel)

If you switch to Pinecone, Qdrant, Milvus, or pgvector,
you replace THIS file — the RAG pipeline just calls the same methods.

What this file does:
    1. Manages the Atlas Vector Search index (create / update)
    2. Stores and retrieves document sources, chunks, and tables
    3. Runs vector similarity searches against stored embeddings
"""

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
    ChunkType,
    IngestionStatus,
)

# Import from infra — this is the canonical location now
from infra.db.mongo import (
    get_chunks_collection,
    get_sources_collection,
    get_tables_collection,
)

# Import the embedding singleton to get vector dimensions
from infra.embeddings.gemini_embeddings import embedding


logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Constants for table chunk fields
# ──────────────────────────────────────────────────────────────

# Fields copied from a table onto each table chunk (the full rows live in `tables`)
TABLE_CHUNK_FIELDS = (
    "table_id", "table_index", "table_part", "row_start", "row_end"
)

# Fields of a structured table stored in the `tables` collection
TABLE_FIELDS = (
    "table_id", "page_number", "table_index", "header", "rows",
    "row_count", "col_count", "bbox", "markdown",
)


class VectorStore:
    """
    Manages document storage and vector similarity search
    using MongoDB Atlas Vector Search.
    """

    def __init__(self, dimensions: int):
        # The number of dimensions each embedding vector has.
        # Injected from the embedder so the index and the vectors
        # can never disagree (e.g. 3072 for gemini-embedding-001).
        self.dimensions = dimensions

    # ──────────────────────────────────────────────────────────
    # Index Management
    # ──────────────────────────────────────────────────────────

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
        Makes sure the Atlas vector index exists AND matches our definition.

        - Creates it if missing
        - Updates it in place if it drifted (no extra index slot used)
        - Raises on failure so the app does not start with a broken index
        """

        collection = get_chunks_collection()
        expected_fields = self._index_fields()

        try:
            # Check if the index already exists
            cursor = collection.list_search_indexes(VECTOR_INDEX_NAME)
            existing = await cursor.to_list(length=1)

            if not existing:
                # Index doesn't exist — create it
                await collection.create_search_index(
                    model=SearchIndexModel(
                        definition={"fields": expected_fields},
                        name=VECTOR_INDEX_NAME,
                        type="vectorSearch"
                    )
                )
                logger.info("Created vector index '%s'.", VECTOR_INDEX_NAME)
                return

            # Index exists — check if it matches our expected definition
            current_fields = (
                existing[0].get("latestDefinition") or {}
            ).get("fields", [])

            if self._normalize(current_fields) == self._normalize(expected_fields):
                logger.info(
                    "Vector index '%s' is up to date.", VECTOR_INDEX_NAME
                )
                return

            # Index exists but definition drifted — update it
            await collection.update_search_index(
                VECTOR_INDEX_NAME,
                {"fields": expected_fields}
            )
            logger.warning(
                "Vector index '%s' definition was out of date; updated it. "
                "Old fields: %s",
                VECTOR_INDEX_NAME,
                current_fields
            )

        except Exception:
            logger.exception("Failed to ensure Atlas Vector Search index.")
            raise

    # ──────────────────────────────────────────────────────────
    # Document Lookups
    # ──────────────────────────────────────────────────────────

    async def get_document_by_hash(
        self,
        file_hash: str
    ) -> dict | None:
        """
        Find a source document by its file hash.
        Returns None if no document with that hash exists.
        """

        collection = get_sources_collection()

        doc = await collection.find_one({
            "file_hash": file_hash
        })

        if not doc:
            return None

        # Convert MongoDB _id to a string field called source_id
        doc["source_id"] = str(doc.pop("_id"))

        return doc

    # ──────────────────────────────────────────────────────────
    # Document Writes
    # ──────────────────────────────────────────────────────────

    async def insert_source(self, document_record: dict) -> str:
        """
        Insert a source document record into the 'sources' collection.
        Returns the new document's ID as a string.
        """

        result = await get_sources_collection().insert_one(document_record)

        return str(result.inserted_id)

    async def insert_chunks(
        self,
        source_id: str,
        filename: str,
        chunks: list[dict],
        embeddings: list[list[float]]
    ) -> int:
        """
        Insert chunks with their embedding vectors into the 'chunks' collection.
        Each chunk is linked to its source document by source_id.
        Returns the number of chunks inserted.
        """

        if len(chunks) != len(embeddings):
            raise ValueError(
                "Number of chunks and embeddings must match."
            )

        # Validate that every embedding has the right number of dimensions
        for emb in embeddings:
            if len(emb) != self.dimensions:
                raise ValueError(
                    f"Expected {self.dimensions} dimensions, "
                    f"received {len(emb)}."
                )

        source_oid = ObjectId(source_id)

        chunk_docs = []

        for chunk, emb in zip(chunks, embeddings, strict=True):

            chunk_type = chunk.get("chunk_type", ChunkType.TEXT.value)

            doc = {
                "source_id": source_oid,
                "filename": filename,
                "chunk_index": chunk["chunk_index"],
                "chunk_type": chunk_type,
                "page_number": chunk["page_number"],
                "text": chunk["text"],
                "embedding": emb
            }

            # Table chunks have extra fields (table_id, table_index, etc.)
            if chunk_type == ChunkType.TABLE:
                doc.update({
                    field: chunk[field] for field in TABLE_CHUNK_FIELDS
                })

            chunk_docs.append(doc)

        if chunk_docs:
            await get_chunks_collection().insert_many(chunk_docs)

        logger.info(
            "Inserted %d chunks for '%s'.",
            len(chunk_docs),
            filename
        )

        return len(chunk_docs)

    async def insert_tables(
        self,
        source_id: str,
        filename: str,
        tables: list[dict]
    ) -> int:
        """
        Store the structured tables of a source in the 'tables' collection.
        Returns the number of tables inserted.
        """

        source_oid = ObjectId(source_id)

        table_docs = [
            {
                "source_id": source_oid,
                "filename": filename,
                **{field: table[field] for field in TABLE_FIELDS}
            }
            for table in tables
        ]

        if table_docs:
            await get_tables_collection().insert_many(table_docs)

        logger.info(
            "Inserted %d tables for '%s'.", len(table_docs), filename
        )

        return len(table_docs)

    async def get_tables(self, source_id: str) -> list[dict]:
        """All structured tables of a source, in page / table order."""

        cursor = get_tables_collection().find(
            {"source_id": ObjectId(source_id)},
            {"_id": 0}
        ).sort([("page_number", 1), ("table_index", 1)])

        tables = await cursor.to_list(length=None)

        for table in tables:
            table["source_id"] = str(table["source_id"])

        return tables

    async def mark_completed(self, source_id: str) -> str:
        """
        Flag a source document as fully ingested.
        Returns the completion timestamp as an ISO string.
        """

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
        """
        Remove a source and all of its chunks and tables.
        Used to clean up partial / failed ingestions.
        """

        source_oid = ObjectId(source_id)

        chunks_result = await get_chunks_collection().delete_many(
            {"source_id": source_oid}
        )
        tables_result = await get_tables_collection().delete_many(
            {"source_id": source_oid}
        )
        await get_sources_collection().delete_one({"_id": source_oid})

        logger.info(
            "Deleted source %s, %d chunks and %d tables.",
            source_id,
            chunks_result.deleted_count,
            tables_result.deleted_count
        )

    # ──────────────────────────────────────────────────────────
    # Vector Retrieval (Similarity Search)
    # ──────────────────────────────────────────────────────────

    async def retrieve(
        self,
        query_embedding: list[float],
        n_results: int = DEFAULT_N_RESULTS,
        document_id: str | None = None
    ) -> list[dict]:
        """
        Find the most similar chunks to a query embedding
        using MongoDB Atlas Vector Search.

        Args:
            query_embedding: The vector to search for.
            n_results:       How many results to return.
            document_id:     Optional — filter to chunks from one document.

        Returns:
            A list of dicts, each with: id, document (text), metadata, score.
        """

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

        # Build the $vectorSearch stage
        vector_search = {
            "index": VECTOR_INDEX_NAME,
            "path": "embedding",
            "queryVector": query_embedding,
            "numCandidates": n_results * NUM_CANDIDATES_MULTIPLIER,
            "limit": n_results
        }

        # Optional: filter to chunks from a specific source document
        if document_id:

            if not ObjectId.is_valid(document_id):
                raise ValueError("Invalid document ID.")

            vector_search["filter"] = {
                VECTOR_FILTER_FIELD: ObjectId(document_id)
            }

        # MongoDB aggregation pipeline
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
                    "chunk_type": 1,
                    "page_number": 1,
                    **{field: 1 for field in TABLE_CHUNK_FIELDS},
                    "score": {
                        "$meta": "vectorSearchScore"
                    }
                }
            }
        ]

        results = []

        async for doc in collection.aggregate(pipeline):

            metadata = {
                "source_id": str(doc["source_id"]),
                "filename": doc["filename"],
                "chunk_index": doc["chunk_index"],
                # Chunks stored before table support have no chunk_type
                "chunk_type": doc.get("chunk_type", ChunkType.TEXT.value),
                "page_number": doc["page_number"]
            }

            # Add table-specific fields if present
            metadata.update({
                field: doc[field]
                for field in TABLE_CHUNK_FIELDS
                if field in doc
            })

            results.append({
                "id": str(doc["_id"]),
                "document": doc["text"],
                "metadata": metadata,
                "score": doc["score"]
            })

        return results


# ──────────────────────────────────────────────────────────────
# Application-level singleton
# ──────────────────────────────────────────────────────────────
# The dimensions come from the embedding provider so they always match.

vector_store = VectorStore(dimensions=embedding.dimensions)
