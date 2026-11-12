import logging

from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import uuid4

import pymupdf

from app.ai.constants import TABLE_MIN_ROWS
from app.core.security import get_file_hash


logger = logging.getLogger(__name__)


# A loader takes a file path and returns the document dict built by
# DocumentLoader._build_document_data (filename, hash, size, pages).
# Every page is {"page_number", "text", "tables"}.
Loader = Callable[[Path], dict]


# ============================================================
# TABLE HELPERS
# ============================================================

def _clean_cell(value) -> str:
    """None -> "", and collapse newlines / repeated spaces inside a cell."""

    return " ".join(str(value).split()) if value is not None else ""


def rows_to_markdown(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Render a header + rows as a GitHub-style markdown table."""

    width = len(header)

    def line(cells: Sequence[str]) -> str:
        cells = list(cells)[:width] + [""] * (width - len(cells))
        return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"

    return "\n".join([
        line(header),
        "| " + " | ".join("---" for _ in range(width)) + " |",
        *(line(row) for row in rows),
    ])


# ============================================================
# 1. DOCUMENT LOADER
# Responsibility: Load files and extract their raw text.
# ============================================================

class DocumentLoader:

    def __init__(self):

        # Extension -> loader registry. To support a new type (e.g. .docx),
        # call register(".docx", my_loader) — no need to edit this class.
        self._loaders: dict[str, Loader] = {
            ".pdf": self.load_pdf,
            ".txt": self.load_txt,
            ".md": self.load_txt,
        }

    # ---------------- registry ----------------

    def register(self, extension: str, loader: Loader) -> None:
        """Add (or replace) the loader used for a file extension."""

        extension = extension.lower()

        if not extension.startswith("."):
            extension = f".{extension}"

        self._loaders[extension] = loader

    @property
    def supported_extensions(self) -> set[str]:
        return set(self._loaders)

    # ---------------- hashing ----------------

    def compute_hash(self, path: str | Path) -> str:
        """Calculate SHA-256 hash of a file."""

        file_bytes = Path(path).read_bytes()

        return get_file_hash(file_bytes)

    @staticmethod
    def _build_document_data(
        path: Path,
        file_bytes: bytes,
        pages: list[dict]
    ) -> dict:
        """Build common document metadata."""

        return {
            "file_path": str(path),
            "filename": path.name,
            "file_hash": get_file_hash(file_bytes),
            "file_size_bytes": len(file_bytes),
            "pages": pages
        }

    # ---------------- loaders ----------------

    def load_txt(self, path: str | Path) -> dict:
        """Extract text from TXT or Markdown files."""

        path = Path(path)
        file_bytes = path.read_bytes()

        text = file_bytes.decode("utf-8")

        pages = []

        if text.strip():
            pages.append({
                "page_number": 1,
                "text": text,
                "tables": []
            })

        return self._build_document_data(
            path=path,
            file_bytes=file_bytes,
            pages=pages
        )

    @staticmethod
    def _extract_tables(page) -> list[dict]:
        """
        Detect tables on a PDF page with PyMuPDF's find_tables().
        Never raises: if detection fails, the page is treated as having no tables
        so normal text extraction still works.
        """

        page_number = page.number + 1

        try:
            found = page.find_tables().tables

        except Exception as error:
            logger.warning("Table detection failed on page %d: %s", page_number, error)
            return []

        tables = []

        for table in found:

            try:
                cells = [[_clean_cell(c) for c in row] for row in table.extract()]
                header = [_clean_cell(name) for name in table.header.names]

                # Header taken from the first table row -> don't repeat it as data
                if not table.header.external and cells:
                    cells = cells[1:]

                header = [name or f"Column {i + 1}" for i, name in enumerate(header)]
                rows = [row for row in cells if any(row)]

            except Exception as error:
                logger.warning("Could not read a table on page %d: %s", page_number, error)
                continue

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

    @staticmethod
    def _text_outside(page, bboxes: list[list[float]]) -> str:
        """
        Page text without the words that belong to a table.

        Works per word (not per block): a word is dropped only if its centre lies
        inside a table area, so a paragraph that shares a block with table cells
        is kept. Limitation: text printed INSIDE a table's box (e.g. a caption
        drawn within the borders) is treated as table content.
        """

        if not bboxes:
            return page.get_text()

        rects = [pymupdf.Rect(b) for b in bboxes]
        lines: dict[tuple[int, int], list[str]] = {}

        for x0, y0, x1, y1, word, block_no, line_no, _ in page.get_text("words"):

            centre = pymupdf.Point((x0 + x1) / 2, (y0 + y1) / 2)

            if any(centre in rect for rect in rects):
                continue

            lines.setdefault((block_no, line_no), []).append(word)

        return "\n".join(" ".join(words) for words in lines.values())

    def load_pdf(self, path: str | Path) -> dict:
        """Extract text and tables from each PDF page."""

        path = Path(path)
        file_bytes = path.read_bytes()

        pages = []

        with pymupdf.open(str(path)) as pdf:

            for page in pdf:

                tables = self._extract_tables(page)
                page_text = self._text_outside(page, [t["bbox"] for t in tables])

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

    def load_file(self, path: str | Path) -> dict | None:
        """Load a file with the loader registered for its extension (None if unsupported)."""

        path = Path(path)
        loader = self._loaders.get(path.suffix.lower())

        return loader(path) if loader else None


# ============================================================
# 2. FILE DISCOVERY
# Responsibility: Find supported files in a folder.
# ============================================================

def list_supported_files(
    folder_path: str | Path,
    extensions: set[str]
) -> list[Path]:

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
