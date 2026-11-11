"""
Single home for every AI / RAG constant.

Change a model, chunk size, limit or retry setting here — no other file
should hard-code these values.
"""

from enum import StrEnum


# ─────────────────────────────────────────────────────────
# Vector store (MongoDB Atlas Vector Search)
# ─────────────────────────────────────────────────────────

VECTOR_INDEX_NAME = "chunks_vector_index"   # must match the index name in Atlas
VECTOR_SIMILARITY = "cosine"
VECTOR_FILTER_FIELD = "source_id"           # chunk field used to filter by document
DEFAULT_N_RESULTS = 5                       # chunks returned per query
NUM_CANDIDATES_MULTIPLIER = 10              # numCandidates = n_results * this


# ─────────────────────────────────────────────────────────
# Embeddings
# ─────────────────────────────────────────────────────────

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSIONS = 3072                 # owned by the embedder; injected into VectorStore


# ─────────────────────────────────────────────────────────
# Chat LLMs
# ─────────────────────────────────────────────────────────

GEMINI_CHAT_MODEL = "gemini-3.5-flash-lite"
GROQ_CHAT_MODEL = "openai/gpt-oss-20b"
GROQ_TEMPERATURE = 1.0


# ─────────────────────────────────────────────────────────
# Chunking
# ─────────────────────────────────────────────────────────

CHUNK_SIZE = 300                            # words per chunk
CHUNK_OVERLAP = 50                          # words repeated between chunks
CHUNKING_VERSION = "v1"                     # bump when chunking logic changes


# ─────────────────────────────────────────────────────────
# Ingestion
# ─────────────────────────────────────────────────────────

class IngestionStatus(StrEnum):
    PENDING = "pending"        # source saved, chunks not confirmed yet
    COMPLETED = "completed"    # source + all chunks saved
    SKIPPED = "skipped"        # already ingested (same file hash)
    EMPTY = "empty"            # no extractable text
    FAILED = "failed"          # error during ingestion


# ─────────────────────────────────────────────────────────
# Chat service
# ─────────────────────────────────────────────────────────

NO_CONTEXT_MESSAGE = "I couldn't find relevant information in the uploaded documents."

CHAT_MAX_RETRIES = 3
CHAT_BASE_RETRY_DELAY = 1.0                 # seconds
CHAT_MAX_RETRY_DELAY = 8.0                  # seconds


# ─────────────────────────────────────────────────────────
# Batch processing
#
#   batch_size          - items sent in one request
#   max_concurrency     - batches allowed to run at the same time
#   requests_per_minute - request limit (None = no limit)
#   tokens_per_minute   - estimated token limit (None = no limit)
#   max_retries         - total attempts per batch before giving up
# ─────────────────────────────────────────────────────────

# Local model: no API limits, and it's CPU-bound, so run one batch at a time
LOCAL_EMBEDDING_SETTINGS = {
    "batch_size": 32,
    "max_concurrency": 1,
    "requests_per_minute": None,
    "tokens_per_minute": None,
    "max_retries": 1,
}

# gemini-embedding-001: kept below the reported free tier limits of
# 100 requests/min and 30k tokens/min. Check the real limits in AI Studio.
GEMINI_FREE_TIER_SETTINGS = {
    "batch_size": 20,
    "max_concurrency": 2,
    "requests_per_minute": 80,
    "tokens_per_minute": 25_000,
    "max_retries": 5,
}

BATCH_BASE_RETRY_DELAY = 5.0                # seconds, first retry waits up to this long
BATCH_MAX_RETRY_DELAY = 60.0                # seconds, longest possible wait between retries
RATE_LIMIT_WINDOW = 60.0                    # seconds, the "per minute" window for the limits
CHARS_PER_TOKEN = 4                         # rough estimate: 1 token is about 4 characters
