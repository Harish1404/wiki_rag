import asyncio
from pathlib import Path

from .chunking import TextChunker
from .document_loader import DocumentLoader, list_supported_files


# ============================================================
#               DOCUMENT PROCESSOR
# Responsibility: Coordinate loading and chunking.
# ============================================================


class DocumentProcessor:

    def __init__(
        self,
        loader: DocumentLoader | None = None,
        chunker: TextChunker | None = None
    ):

        self.loader = loader or DocumentLoader()
        self.chunker = chunker or TextChunker()

    def compute_hash(self, path: str | Path) -> str:
        """Delegate file hashing to DocumentLoader."""

        return self.loader.compute_hash(path)

    def list_files(self, folder_path: str | Path) -> list[Path]:
        """Find supported files."""

        return list_supported_files(
            folder_path,
            self.loader.supported_extensions
        )

    def _process_file(self, path: str | Path) -> dict:
        """Load a file and generate chunks."""

        # Step 1: Load the file
        content = self.loader.load_file(path)

        if content is None:
            raise ValueError(f"Unsupported file type: {path}")

        pages = content["pages"]

        # Step 2: Text chunks, then table chunks (indexes continue after the text)
        text_chunks = self.chunker.chunk_pages(pages)
        table_chunks = self.chunker.chunk_tables(pages, start_index=len(text_chunks))

        chunks = text_chunks + table_chunks

        # Step 3: Attach chunks and the structured tables to the document
        content["chunks"] = chunks
        content["total_chunks"] = len(chunks)
        content["tables"] = [table for page in pages for table in page.get("tables", [])]

        return content

    async def process_file(self, path: str | Path) -> dict:
        """Run file processing outside the main async event loop."""

        return await asyncio.to_thread(
            self._process_file,
            path
        )


# ============================================================
# 5. APPLICATION OBJECT
# ============================================================

data_processor = DocumentProcessor()
