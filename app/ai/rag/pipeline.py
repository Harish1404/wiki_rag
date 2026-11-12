import logging

from pathlib import Path

from app.ai.constants import CHUNKING_VERSION, DEFAULT_N_RESULTS, IngestionStatus
from app.ai.prompts.gemini_prompt import CONTEXT_BLOCK_PROMPT
from app.ai.rag.document_processing.processor import data_processor
from app.ai.rag.embed import embedding
from app.ai.rag.vector_store import vector_store


logger = logging.getLogger(__name__)


class RagPipeline:

    def __init__(
        self,
        data_processor,
        embedding,
        vector_store
    ):

        self.data_processor = data_processor
        self.embedding = embedding
        self.vector_store = vector_store

    # ----------------------------------
    # Document Ingestion
    # ----------------------------------

    async def ingest_file(self, path: str | Path):

        path = Path(path)

        # 1. Calculate file hash
        file_hash = self.data_processor.compute_hash(path)

        # 2. Check whether document already exists
        existing_doc = await self.vector_store.get_document_by_hash(
            file_hash
        )

        if existing_doc:

            # Records from before status tracking have no status; treat them as complete
            status = existing_doc.get("ingestion_status", IngestionStatus.COMPLETED)

            if status == IngestionStatus.COMPLETED:
                return {
                    "status": IngestionStatus.SKIPPED,
                    "message": (
                        f"File '{path.name}' already ingested as "
                        f"'{existing_doc.get('filename')}'."
                    ),
                    "document": existing_doc
                }

            # A previous run stopped half-way: remove the partial data and redo it
            logger.warning(
                "Found incomplete ingestion of '%s' (status=%s); cleaning up and retrying.",
                path.name,
                status
            )
            await self.vector_store.delete_document(existing_doc["source_id"])

        # 3. Process document
        content = await self.data_processor.process_file(path)

        chunks = content["chunks"]

        if not chunks:
            return {
                "status": IngestionStatus.EMPTY,
                "message": f"File '{path.name}' has no extractable text.",
                "document": None
            }

        # 4. Generate embeddings (before any DB write, so most failures leave nothing behind)
        chunk_texts = [chunk["text"] for chunk in chunks]

        embeddings = await self.embedding.embed_chunks(chunk_texts)

        if len(chunks) != len(embeddings):
            raise ValueError(
                "Number of chunks and generated embeddings do not match."
            )

        # 5. Save the source as PENDING first
        document_record = {
            "filename": content["filename"],
            "file_hash": file_hash,
            "file_size_bytes": content["file_size_bytes"],
            "ingestion_status": IngestionStatus.PENDING.value,
            "chunk_count": len(chunks),
            "chunking_version": CHUNKING_VERSION,
            "embedding": {
                "model_name": self.embedding.model_name,
                "dimensions": self.embedding.dimensions
            }
        }

        source_id = await self.vector_store.insert_source(document_record)

        # 6. Save chunks, then mark the source COMPLETED.
        #    If anything fails in between, the source stays PENDING and the
        #    next run cleans it up (step 2) instead of skipping it forever.
        await self.vector_store.insert_chunks(
            source_id=source_id,
            filename=content["filename"],
            chunks=chunks,
            embeddings=embeddings
        )

        ingested_at = await self.vector_store.mark_completed(source_id)

        logger.info(
            "Successfully ingested '%s' with %d chunks.",
            path.name,
            len(chunks)
        )

        return {
            "status": IngestionStatus.COMPLETED,
            "message": (
                f"File '{path.name}' ingested with "
                f"{len(chunks)} chunks."
            ),
            "document": {
                **document_record,
                "ingestion_status": IngestionStatus.COMPLETED.value,
                "ingested_at": ingested_at,
                "source_id": source_id
            }
        }

    # ----------------------------------
    # Folder Ingestion
    # ----------------------------------

    async def data_ingestion(
        self,
        folder_path: str | Path = "uploads"
    ):

        files = self.data_processor.list_files(folder_path)

        summary = {
            "total": len(files),
            "ingested": 0,
            "skipped": 0,
            "failed": 0,
            "results": []
        }

        if not files:
            logger.info("No files to ingest in '%s'.", folder_path)
            return summary

        for path in files:

            try:
                result = await self.ingest_file(path)

            except Exception as e:
                logger.exception(
                    "Failed to ingest '%s'.",
                    path.name
                )

                result = {
                    "status": IngestionStatus.FAILED,
                    "message": str(e),
                    "document": None
                }

            status = result["status"]

            if status == IngestionStatus.COMPLETED:
                summary["ingested"] += 1

            elif status in (IngestionStatus.SKIPPED, IngestionStatus.EMPTY):
                summary["skipped"] += 1

            else:
                summary["failed"] += 1

            logger.info("[%s] %s", status, result["message"])

            summary["results"].append({
                "file": path.name,
                **result
            })

        logger.info(
            "Ingestion finished: %d ingested, %d skipped, "
            "%d failed (of %d files).",
            summary["ingested"],
            summary["skipped"],
            summary["failed"],
            summary["total"]
        )

        return summary

    # ----------------------------------
    # Retrieval
    # ----------------------------------

    async def retrieve(
        self,
        query: str,
        n_results: int = DEFAULT_N_RESULTS,
        document_id: str | None = None
    ):

        query_embedding = await self.embedding.embed_query(query)

        results = await self.vector_store.retrieve(
            query_embedding=query_embedding,
            n_results=n_results,
            document_id=document_id
        )

        return {
            "query": query,
            "total_results": len(results),
            "results": results
        }

    # ----------------------------------
    # Context Building
    # ----------------------------------

    def build_context(
        self,
        results: list[dict]
    ) -> tuple[str, list[dict]]:

        context_blocks = []
        sources = []
        seen = set()

        for result in results:

            meta = result.get("metadata") or {}

            filename = meta.get("filename", "unknown")
            page_number = meta.get("page_number")

            context_blocks.append(
                CONTEXT_BLOCK_PROMPT.format(
                    filename=filename,
                    page_number=page_number,
                    text=result["document"]
                )
            )

            source_key = (filename, page_number)

            if source_key not in seen:

                seen.add(source_key)

                sources.append({
                    "filename": filename,
                    "page_number": page_number,
                    "score": result.get("score")
                })

        return "\n\n".join(context_blocks), sources


# ----------------------------------
# Application-level instance
# ----------------------------------

rag_pipeline = RagPipeline(
    data_processor=data_processor,
    embedding=embedding,
    vector_store=vector_store
)
