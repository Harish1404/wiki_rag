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

        # Step 2: Generate chunks
        chunks = self.chunker.chunk_pages(content["pages"])

        # Step 3: Attach chunks to the document
        content["chunks"] = chunks
        content["total_chunks"] = len(chunks)

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
