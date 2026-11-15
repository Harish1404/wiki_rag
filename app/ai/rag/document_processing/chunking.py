from app.ai.constants import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    TABLE_ROWS_PER_CHUNK,
    ChunkType,
)

from .document_loader import rows_to_markdown


# ============================================================
# 2. TEXT CHUNKER
# Responsibility: Split extracted text (and tables) into chunks.
# ============================================================

class TextChunker:

    def __init__(
        self,
        chunk_size: int = CHUNK_SIZE,
        overlap: int = CHUNK_OVERLAP,
        table_rows_per_chunk: int = TABLE_ROWS_PER_CHUNK
    ):
        if chunk_size <= 0:
            raise ValueError("Chunk size must be greater than zero.")

        if overlap < 0 or overlap >= chunk_size:
            raise ValueError(
                "Overlap must be non-negative and smaller than chunk size."
            )

        if table_rows_per_chunk <= 0:
            raise ValueError("Table rows per chunk must be greater than zero.")

        self.chunk_size = chunk_size
        self.overlap = overlap
        self.table_rows_per_chunk = table_rows_per_chunk

    def chunk_pages(self, pages: list[dict]) -> list[dict]:
        """Split page text into overlapping chunks."""

        chunks = []
        chunk_index = 0

        step = self.chunk_size - self.overlap

        for page in pages:

            page_number = page["page_number"]
            words = page.get("text", "").split()

            if not words:
                continue

            for start in range(0, len(words), step):

                chunk_words = words[
                    start : start + self.chunk_size
                ]

                chunks.append({
                    "chunk_index": chunk_index,
                    "chunk_type": ChunkType.TEXT.value,
                    "page_number": page_number,
                    "text": " ".join(chunk_words)
                })

                chunk_index += 1

                if start + self.chunk_size >= len(words):
                    break

        return chunks

    def chunk_tables(self, pages: list[dict], start_index: int = 0) -> list[dict]:
        """
        One or more markdown chunks per table, split by rows.
        Every part repeats the header so the columns stay meaningful on their own.
        Chunks carry only a reference (table_id) — the full rows live in `tables`.
        """

        chunks = []
        chunk_index = start_index
        size = self.table_rows_per_chunk

        for page in pages:

            for table in page.get("tables", []):

                rows = table["rows"]
                total = len(rows)

                for part, offset in enumerate(range(0, total, size), start=1):

                    row_start = offset + 1                   # 1-based, inclusive
                    row_end = min(offset + size, total)

                    title = (
                        f"Table {table['table_index'] + 1} on page {table['page_number']} "
                        f"(rows {row_start}-{row_end} of {total}):"
                    )

                    chunks.append({
                        "chunk_index": chunk_index,
                        "chunk_type": ChunkType.TABLE.value,
                        "page_number": table["page_number"],
                        "table_id": table["table_id"],
                        "table_index": table["table_index"],
                        "table_part": part,
                        "row_start": row_start,
                        "row_end": row_end,
                        "text": title + "\n" + rows_to_markdown(
                            table["header"], rows[offset : offset + size]
                        )
                    })

                    chunk_index += 1

        return chunks
