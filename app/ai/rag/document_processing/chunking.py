from app.ai.constants import CHUNK_OVERLAP, CHUNK_SIZE


# ============================================================
# 2. TEXT CHUNKER
# Responsibility: Split extracted text into chunks.
# ============================================================

class TextChunker:

    def __init__(
        self,
        chunk_size: int = CHUNK_SIZE,
        overlap: int = CHUNK_OVERLAP
    ):
        if chunk_size <= 0:
            raise ValueError("Chunk size must be greater than zero.")

        if overlap < 0 or overlap >= chunk_size:
            raise ValueError(
                "Overlap must be non-negative and smaller than chunk size."
            )

        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk_pages(self, pages: list[dict]) -> list[dict]:
        """Split page text into overlapping chunks."""

        chunks = []
        chunk_index = 0

        step = self.chunk_size - self.overlap

        for page in pages:

            page_number = page["page_number"]
            words = page["text"].split()

            if not words:
                continue

            for start in range(0, len(words), step):

                chunk_words = words[
                    start : start + self.chunk_size
                ]

                chunks.append({
                    "chunk_index": chunk_index,
                    "page_number": page_number,
                    "text": " ".join(chunk_words)
                })

                chunk_index += 1

                if start + self.chunk_size >= len(words):
                    break

        return chunks

