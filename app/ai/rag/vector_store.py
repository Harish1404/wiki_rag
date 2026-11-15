"""
Bridge file: the real VectorStore lives in infra/db/mongo_vector_store.py.
This file re-exports everything so existing imports still work.

Example:
    from app.ai.rag.vector_store import vector_store   # still works!
    # It just fetches the object from infra/db/mongo_vector_store.py now.
"""

from infra.db.mongo_vector_store import VectorStore, vector_store   # noqa: F401
