"""
Bridge file: the real Embedding lives in infra/embeddings/gemini_embeddings.py.
This file re-exports everything so existing imports still work.

Example:
    from app.ai.rag.embed import embedding   # still works!
    # It just fetches the object from infra/embeddings/gemini_embeddings.py now.
"""

from infra.embeddings.gemini_embeddings import (   # noqa: F401 — re-exported
    GeminiEmbeddings,
    Embedding,
    embedding,
)
