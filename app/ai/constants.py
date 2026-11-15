"""
Single home for every AI / RAG constant.

Change a model, chunk size, limit or retry setting here — no other file
should hard-code these values.
"""

from enum import Enum


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

# Fallback order: primary_llm (Groq) -> llm2 (Gemini) -> llm3 (Mistral)
GROQ_CHAT_MODEL = "openai/gpt-oss-20b"
GEMINI_CHAT_MODEL = "gemini-3.5-flash-lite"
MISTRAL_CHAT_MODEL = "mistral-small-latest"

LLM_TEMPERATURE = 1.0
LLM_TIMEOUT = 60                            # seconds per model request


# ─────────────────────────────────────────────────────────
# Chunking
# ─────────────────────────────────────────────────────────

CHUNK_SIZE = 300                            # words per chunk
CHUNK_OVERLAP = 50                          # words repeated between chunks
CHUNKING_VERSION = "v2"                     # bump when chunking logic changes (v2: PDF tables)


class ChunkType(str, Enum):
    TEXT = "text"              # regular page text
    TABLE = "table"            # markdown rendering of (part of) a PDF table


# ─────────────────────────────────────────────────────────
# Table extraction (PDF)
# ─────────────────────────────────────────────────────────

TABLE_MIN_ROWS = 1                          # data rows needed to keep a detected table
TABLE_ROWS_PER_CHUNK = 20                   # rows per table chunk; header repeated in every part


# ─────────────────────────────────────────────────────────
# Ingestion
# ─────────────────────────────────────────────────────────

class IngestionStatus(str, Enum):
    PENDING = "pending"        # source saved, chunks not confirmed yet
    COMPLETED = "completed"    # source + all chunks saved
    SKIPPED = "skipped"        # already ingested (same file hash)
    EMPTY = "empty"            # no extractable text
    FAILED = "failed"          # error during ingestion


# ─────────────────────────────────────────────────────────
# Chat service
# ─────────────────────────────────────────────────────────

NO_CONTEXT_MESSAGE = "I couldn't find relevant information in the uploaded documents."

# ModelRetryMiddleware: retries per model before falling back to the next one
CHAT_MAX_RETRIES = 3                        # retries after the first attempt
CHAT_BASE_RETRY_DELAY = 1.0                 # seconds
CHAT_MAX_RETRY_DELAY = 8.0                  # seconds
CHAT_BACKOFF_FACTOR = 2.0

# Runnable.with_retry on document retrieval (total attempts)
RETRIEVAL_MAX_ATTEMPTS = 3


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


# ─────────────────────────────────────────────────────────
# Query router
# ─────────────────────────────────────────────────────────

class Route(str, Enum):
    RAG = "RAG"            # questions about Virat Kohli -> document search
    TOOL = "TOOL"          # weather / INR->USD -> tool agent
    DIRECT = "DIRECT"      # general questions -> model's own knowledge


DEFAULT_ROUTE = Route.RAG                   # used when every router model fails
ROUTER_TEMPERATURE = 0.0                    # routing must be deterministic
ROUTER_MAX_ATTEMPTS = 2                     # attempts per router model before falling back


# ─────────────────────────────────────────────────────────
# Tools
# ─────────────────────────────────────────────────────────

INR_PER_USD = 93.75                         # rate used by inr_to_usd; update as needed
TOOL_MAX_RETRIES = 2                        # ToolRetryMiddleware on the tool agent

OPEN_METEO_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HTTP_TIMEOUT = 10                           # seconds for tool HTTP calls

SSE_TOOL_OUTPUT_LIMIT = 500                 # max chars of tool output sent to the frontend

