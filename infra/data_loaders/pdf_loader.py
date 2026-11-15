"""
Document loaders: PDF and plain-text file readers.

This file is the ONLY place that knows about PyMuPDF (the PDF library).
If you want to add a new file type (e.g. .docx, .csv), either:
    - Add a new loader method here and register it, or
    - Create a new file in infra/data_loaders/ and register it.

How it works:
    1. DocumentLoader has a registry of file extension -> loader function
    2. load_file() picks the right loader based on the file extension
    3. Each loader returns a dict with: filename, file_hash, file_size, pages
    4. Each page has: page_number, text, tables
    5. list_supported_files() finds all loadable files in a folder
"""

import logging

from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import uuid4

import pymupdf

from app.ai.constants import TABLE_MIN_ROWS
from app.core.security import get_file_hash


logger = logging.getLogger(__name__)


# A "Loader" is any function that takes a file path and returns
# the document dict (filename, hash, size, pages).
Loader = Callable[[Path], dict]


# ──────────────────────────────────────────────────────────────
# Table helpers
# ──────────────────────────────────────────────────────────────
# These turn raw table data into clean markdown.

def _clean_cell(value) -> str:
    """
    Clean a single table cell:
    - None becomes an empty string
    - Collapses newlines and repeated spaces
    """
    return " ".join(str(value).split()) if value is not None else ""


def rows_to_markdown(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """
    Render a header + rows as a GitHub-style markdown table.

    Example output:
        | Name | Score |
        | --- | --- |
        | Kohli | 100 |
    """

    width = len(header)

    def line(cells: Sequence[str]) -> str:
        # Pad or trim to match header width
        cells = list(cells)[:width] + [""] * (width - len(cells))
        return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"

    return "\n".join([
        line(header),
        "| " + " | ".join("---" for _ in range(width)) + " |",
        *(line(row) for row in rows),
    ])


# ──────────────────────────────────────────────────────────────
# DocumentLoader
# ──────────────────────────────────────────────────────────────

class DocumentLoader:
    """
    Loads files and extracts their raw text + tables.

    Supports .pdf, .txt, and .md out of the box.
    Call register() to add more file types without editing this class.
    """

    def __init__(self):
        # Extension -> loader function registry.
        # To support a new type (e.g. .docx), call:
        #   loader.register(".docx", my_docx_loader)
        self._loaders: dict[str, Loader] = {
            ".pdf": self.load_pdf,
            ".txt": self.load_txt,
            ".md": self.load_txt,    # Markdown is treated like plain text
        }

    # ─────── Registry methods ───────

    def register(self, extension: str, loader: Loader) -> None:
        """Add (or replace) the loader used for a file extension."""
        extension = extension.lower()
        if not extension.startswith("."):
            extension = f".{extension}"

        self._loaders[extension] = loader
        logger.info("Registered loader for '%s'.", extension)

    @property
    def supported_extensions(self) -> set[str]:
        """All file extensions we can currently load."""
        return set(self._loaders)

    # ─────── File hashing ───────

    def compute_hash(self, path: str | Path) -> str:
        """
        Calculate SHA-256 hash of a file.
        Used to detect if a file has already been ingested.
        """
        file_bytes = Path(path).read_bytes()
        return get_file_hash(file_bytes)

    # ─────── Internal helpers ───────

    @staticmethod
    def _build_document_data(
        path: Path,
        file_bytes: bytes,
        pages: list[dict]
    ) -> dict:
        """
        Build the standard document metadata dict.
        Every loader must return a dict in this format.
        """
        return {
            "file_path": str(path),
            "filename": path.name,
            "file_hash": get_file_hash(file_bytes),
            "file_size_bytes": len(file_bytes),
            "pages": pages
        }

    # ─────── Plain text loader ───────

    def load_txt(self, path: str | Path) -> dict:
        """
        Extract text from TXT or Markdown files.
        The entire file becomes one "page".
        """
        path = Path(path)
        file_bytes = path.read_bytes()
        text = file_bytes.decode("utf-8")

        pages = []

        if text.strip():
            pages.append({
                "page_number": 1,
                "text": text,
                "tables": []       # Plain text files have no tables
            })

        return self._build_document_data(
            path=path,
            file_bytes=file_bytes,
            pages=pages
        )

    # ─────── PDF table extraction ───────

    @staticmethod
    def _extract_tables(page) -> list[dict]:
        """
        Detect tables on a PDF page using PyMuPDF's find_tables().

        Never raises: if detection fails, the page is treated as
        having no tables so normal text extraction still works.
        """

        page_number = page.number + 1

        try:
            found = page.find_tables().tables

        except Exception as error:
            logger.warning(
                "Table detection failed on page %d: %s",
                page_number, error
            )
            return []

        tables = []

        for table in found:

            try:
                # Extract all cells and clean them
                cells = [[_clean_cell(c) for c in row] for row in table.extract()]
                header = [_clean_cell(name) for name in table.header.names]

                # Header taken from the first table row -> don't repeat as data
                if not table.header.external and cells:
                    cells = cells[1:]

                # Give unnamed columns a default name
                header = [name or f"Column {i + 1}" for i, name in enumerate(header)]

                # Remove completely empty rows
                rows = [row for row in cells if any(row)]

            except Exception as error:
                logger.warning(
                    "Could not read a table on page %d: %s",
                    page_number, error
                )
                continue

            # Skip tables that are too small
            if len(rows) < TABLE_MIN_ROWS:
                continue

            tables.append({
                "table_id": str(uuid4()),
                "table_index": len(tables),
                "page_number": page_number,
                "bbox": [float(x) for x in table.bbox],
                "header": header,
                "rows": rows,
                "row_count": len(rows),
                "col_count": len(header),
                "markdown": rows_to_markdown(header, rows),
            })

        return tables

    # ─────── PDF text extraction (excluding table areas) ───────

    @staticmethod
    def _text_outside(page, bboxes: list[list[float]]) -> str:
        """
        Get all text from a PDF page EXCEPT text inside table areas.

        Works per word: a word is dropped only if its centre lies
        inside a table bounding box.
        """

        if not bboxes:
            return page.get_text()

        rects = [pymupdf.Rect(b) for b in bboxes]
        lines: dict[tuple[int, int], list[str]] = {}

        for x0, y0, x1, y1, word, block_no, line_no, _ in page.get_text("words"):

            centre = pymupdf.Point((x0 + x1) / 2, (y0 + y1) / 2)

            # Skip words whose centre is inside any table
            if any(centre in rect for rect in rects):
                continue

            lines.setdefault((block_no, line_no), []).append(word)

        return "\n".join(" ".join(words) for words in lines.values())

    # ─────── PDF loader ───────

    def load_pdf(self, path: str | Path) -> dict:
        """
        Extract text and tables from each PDF page.

        For each page:
        1. Detect tables (bounding boxes + structured data)
        2. Extract text OUTSIDE the table areas
        3. Return both together
        """

        path = Path(path)
        file_bytes = path.read_bytes()

        pages = []

        with pymupdf.open(str(path)) as pdf:

            for page in pdf:

                tables = self._extract_tables(page)
                page_text = self._text_outside(
                    page, [t["bbox"] for t in tables]
                )

                if (page_text and page_text.strip()) or tables:
                    pages.append({
                        "page_number": page.number + 1,
                        "text": page_text,
                        "tables": tables
                    })

        return self._build_document_data(
            path=path,
            file_bytes=file_bytes,
            pages=pages
        )

    # ─────── Main entry point ───────

    def load_file(self, path: str | Path) -> dict | None:
        """
        Load a file using the loader registered for its extension.
        Returns None if the file type is not supported.
        """
        path = Path(path)
        loader = self._loaders.get(path.suffix.lower())

        return loader(path) if loader else None


# ──────────────────────────────────────────────────────────────
# File discovery
# ──────────────────────────────────────────────────────────────

def list_supported_files(
    folder_path: str | Path,
    extensions: set[str]
) -> list[Path]:
    """
    Find all files in a folder that have a supported extension.
    Creates the folder if it doesn't exist.
    Returns a sorted list of file paths.
    """
    folder = Path(folder_path)

    if not folder.exists():
        folder.mkdir(parents=True, exist_ok=True)
        return []

    return sorted(
        path
        for path in folder.iterdir()
        if path.is_file()
        and path.suffix.lower() in extensions
    )
