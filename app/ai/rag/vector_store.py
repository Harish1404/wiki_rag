import logging
from app.db.mongo import get_chunks_collection, get_sources_collection
from bson import ObjectId
from pymongo.operations import SearchIndexModel

logger = logging.getLogger("uvicorn.error")

# Atlas Vector Search configuration
INDEX_NAME = "chunk_vector_index"
EMBEDDING_DIMENSIONS = 3072  # gemini-embedding-001


class VectorStore:

    # ── Index Management ──────────────────────────────────────

    async def ensure_vector_index(self):
        """Checks for the Atlas Vector Search index and creates it if missing."""
        try:
            collection = get_chunks_collection()
            cursor = collection.list_search_indexes()
            existing_indexes = await cursor.to_list(length=100)
            idx_names = [idx.get("name") for idx in existing_indexes]

            if INDEX_NAME not in idx_names:
                index_model = SearchIndexModel(
                    definition={
                        "fields": [
                            {
                                "type": "vector",
                                "path": "embedding",
                                "numDimensions": EMBEDDING_DIMENSIONS,
                                "similarity": "cosine"
                            }
                        ]
                    },
                    name=INDEX_NAME,
                    type="vectorSearch"
                )
                await collection.create_search_index(model=index_model)
                logger.info(
                    f"✅ Created Atlas Vector Search index '{INDEX_NAME}'."
                )
            else:
                logger.info(
                    f"✅ Atlas Vector Search index '{INDEX_NAME}' already exists."
                )

        except Exception as e:
            logger.warning(f"Atlas Search index check note: {e}")

    # ── Lookups ───────────────────────────────────────────────

    async def get_document_by_hash(self, file_hash: str) -> dict | None:
        """Finds a source document by its SHA-256 file hash."""
        collection = get_sources_collection()
        doc = await collection.find_one({"file_hash": file_hash})
        if doc:
            doc["source_id"] = str(doc.pop("_id"))
            return doc
        return None

    async def get_document_by_id(self, source_id: str) -> dict | None:
        """Finds a source document by its _id."""
        collection = get_sources_collection()
        doc = await collection.find_one({"_id": ObjectId(source_id)})
        if doc:
            doc["source_id"] = str(doc.pop("_id"))
            return doc
        return None

    # ── Insert ────────────────────────────────────────────────

    async def insert(
        self,
        document_record: dict,
        chunks: list[dict],
        embeddings: list[list[float]]
    ) -> str:
        """
        Inserts one source document record and its chunks (with embeddings).
        Returns the source document's ObjectId as a string.
        """
        sources = get_sources_collection()
        chunks_col = get_chunks_collection()

        # 1. Insert the source document record (MongoDB auto-generates _id)
        result = await sources.insert_one(document_record)
        source_id = result.inserted_id

        # 2. Insert chunks with their embeddings
        if chunks and embeddings:
            chunk_docs = []
            for chunk, emb in zip(chunks, embeddings):
                chunk_docs.append({
                    "source_id": source_id,
                    "filename": document_record["filename"],
                    "chunk_index": chunk["chunk_index"],
                    "page_number": chunk["page_number"],
                    "text": chunk["text"],
                    "embedding": emb,
                })
            await chunks_col.insert_many(chunk_docs)

        logger.info(
            f"Inserted source '{document_record['filename']}' "
            f"with {len(chunks)} chunks into MongoDB."
        )
        return str(source_id)

    # ── Vector Search (Retrieval) ─────────────────────────────

    async def retrieve(
        self,
        query_embedding: list[float],
        n_results: int = 5,
        document_id: str | None = None
    ) -> list[dict]:
        """
        Performs Atlas Vector Search on the chunks collection.
        Optionally filters by source_id.
        Returns a list of dicts with id, document (text), metadata, and score.
        """
        collection = get_chunks_collection()

        # Build the $vectorSearch aggregation stage
        vector_search_stage = {
            "$vectorSearch": {
                "index": INDEX_NAME,
                "path": "embedding",
                "queryVector": query_embedding,
                "numCandidates": n_results * 10,
                "limit": n_results,
            }
        }

        # Optional pre-filter by source_id
        if document_id:
            vector_search_stage["$vectorSearch"]["filter"] = {
                "source_id": ObjectId(document_id)
            }

        pipeline = [
            vector_search_stage,
            {
                "$project": {
                    "_id": 1,
                    "text": 1,
                    "source_id": 1,
                    "filename": 1,
                    "chunk_index": 1,
                    "page_number": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            }
        ]

        results = []
        async for doc in collection.aggregate(pipeline):
            results.append({
                "id": str(doc.get("_id")),
                "document": doc.get("text"),
                "metadata": {
                    "source_id": str(doc.get("source_id")),
                    "filename": doc.get("filename"),
                    "chunk_index": doc.get("chunk_index"),
                    "page_number": doc.get("page_number"),
                },
                "distance": doc.get("score"),
            })

        return results


vector_store = VectorStore()
