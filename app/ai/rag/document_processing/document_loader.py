from collections.abc import Callable
from pathlib import Path

import pymupdf

from app.core.security import get_file_hash


# A loader takes a file path and returns the document dict built by
# DocumentLoader._build_document_data (filename, hash, size, pages).
Loader = Callable[[Path], dict]


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
                "text": text
            })

        return self._build_document_data(
            path=path,
            file_bytes=file_bytes,
            pages=pages
        )

    def load_pdf(self, path: str | Path) -> dict:
        """Extract text from each PDF page."""

        path = Path(path)
        file_bytes = path.read_bytes()

        pages = []

        with pymupdf.open(str(path)) as pdf:

            for page in pdf:

                page_text = page.get_text()

                if page_text and page_text.strip():
                    pages.append({
                        "page_number": page.number + 1,
                        "text": page_text
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
