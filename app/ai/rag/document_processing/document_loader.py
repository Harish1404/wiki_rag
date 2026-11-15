"""
Bridge file: the real DocumentLoader lives in infra/data_loaders/pdf_loader.py.
This file re-exports everything so existing imports still work.

Example:
    from app.ai.rag.document_processing.document_loader import DocumentLoader
    # still works! It just fetches from infra/data_loaders/pdf_loader.py now.
"""

from infra.data_loaders.pdf_loader import (   # noqa: F401 — re-exported
    DocumentLoader,
    list_supported_files,
    rows_to_markdown,
    _clean_cell,
)
