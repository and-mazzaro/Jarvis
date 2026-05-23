"""
Document ingestor — loads PDF / TXT / DOCX files, chunks them, and stores
embeddings in a persistent ChromaDB collection.
"""
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

logger = logging.getLogger("jarvis.rag.ingestor")

# --------------------------------------------------------------------------- #
# Optional heavy imports
# --------------------------------------------------------------------------- #
try:
    import fitz as pymupdf  # PyMuPDF
    _HAVE_PDF = True
except ImportError:
    _HAVE_PDF = False
    logger.warning("PyMuPDF not installed — PDF ingestion disabled.")

try:
    from docx import Document as DocxDocument
    _HAVE_DOCX = True
except ImportError:
    _HAVE_DOCX = False
    logger.warning("python-docx not installed — DOCX ingestion disabled.")


# --------------------------------------------------------------------------- #
# Ingestor
# --------------------------------------------------------------------------- #

class Ingestor:
    def __init__(self, config: dict, chroma_path: Path, embedder=None, chroma_client=None):
        self.collection_name: str = config.get("collection_name", "jarvis_knowledge")
        self.chunk_size: int = config.get("chunk_size", 500)
        self.chunk_overlap: int = config.get("chunk_overlap", 50)

        if embedder is not None:
            self._embedder = embedder
        else:
            self._embedder = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")

        if chroma_client is not None:
            self._client = chroma_client
        else:
            chroma_path.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=str(chroma_path),
                settings=Settings(anonymized_telemetry=False),
            )
            
        self._col = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "ChromaDB ready — collection '%s' (%d docs).",
            self.collection_name,
            self._col.count(),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ingest_file(self, file_path: str | Path) -> dict:
        """
        Ingest a single file.  Returns a summary dict with keys:
          file, chunks_added, already_indexed
        """
        path = Path(file_path).resolve()
        file_id = _file_id(path)

        # Skip if already indexed (same content hash)
        existing = self._col.get(where={"file_id": file_id}, limit=1)
        if existing["ids"]:
            logger.info("%s already indexed — skipping.", path.name)
            return {"file": path.name, "chunks_added": 0, "already_indexed": True}

        # Delete any old chunks belonging to the same path (prevent orphans on update)
        existing_by_path = self._col.get(where={"file_path": str(path)})
        if existing_by_path["ids"]:
            self._col.delete(ids=existing_by_path["ids"])
            logger.info("Removed %d existing chunks for %s before re-indexing.", len(existing_by_path["ids"]), path.name)

        text = self._load_text(path)
        if not text.strip():
            return {"file": path.name, "chunks_added": 0, "already_indexed": False}

        chunks = self._split_chunks(text)
        if not chunks:
            return {"file": path.name, "chunks_added": 0, "already_indexed": False}

        embeddings = self._embedder.encode(chunks, show_progress_bar=False).tolist()
        now = datetime.now(timezone.utc).isoformat()

        ids = [f"{file_id}_{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "file_id": file_id,
                "file_name": path.name,
                "file_path": str(path),
                "chunk_index": i,
                "date_added": now,
            }
            for i in range(len(chunks))
        ]

        self._col.add(
            ids=ids,
            documents=chunks,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        logger.info("Indexed %d chunks from %s.", len(chunks), path.name)
        return {"file": path.name, "chunks_added": len(chunks), "already_indexed": False}

    def delete_file(self, file_name: str) -> int:
        """Delete all chunks belonging to *file_name*.  Returns deleted count."""
        results = self._col.get(where={"file_name": file_name})
        ids = results["ids"]
        if ids:
            self._col.delete(ids=ids)
            logger.info("Deleted %d chunks for %s.", len(ids), file_name)
        return len(ids)

    def list_files(self) -> list[dict]:
        """Return a list of indexed files with metadata."""
        results = self._col.get(include=["metadatas"])
        seen: dict[str, dict] = {}
        for meta in results["metadatas"]:
            fname = meta.get("file_name", "unknown")
            if fname not in seen:
                seen[fname] = {
                    "file_name": fname,
                    "file_path": meta.get("file_path", ""),
                    "date_added": meta.get("date_added", ""),
                    "chunk_count": 0,
                }
            seen[fname]["chunk_count"] += 1
        return list(seen.values())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_text(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".txt":
            return path.read_text(encoding="utf-8", errors="ignore")
        elif suffix == ".pdf":
            if not _HAVE_PDF:
                raise RuntimeError("PyMuPDF required for PDF ingestion.")
            with pymupdf.open(str(path)) as doc:
                return "\n".join(page.get_text() for page in doc)
        elif suffix == ".docx":
            if not _HAVE_DOCX:
                raise RuntimeError("python-docx required for DOCX ingestion.")
            doc = DocxDocument(str(path))
            return "\n".join(p.text for p in doc.paragraphs)
        elif suffix == ".doc":
            raise ValueError("Il formato legacy .doc non è supportato direttamente. Convertilo in .docx prima di procedere.")
        else:
            raise ValueError(f"Unsupported file type: {suffix}")

    def _split_chunks(self, text: str) -> list[str]:
        """Naive word-count chunker with overlap."""
        words = text.split()
        chunks: list[str] = []
        start = 0
        while start < len(words):
            end = min(start + self.chunk_size, len(words))
            chunk = " ".join(words[start:end]).strip()
            if chunk:
                chunks.append(chunk)
            if end == len(words):
                break
            start += self.chunk_size - self.chunk_overlap
        return chunks


# --------------------------------------------------------------------------- #
# Helper
# --------------------------------------------------------------------------- #

def _file_id(path: Path) -> str:
    """Stable ID based on file path hash + content SHA-256 hash."""
    abs_path = str(path.resolve())
    path_hash = hashlib.sha256(abs_path.encode("utf-8")).hexdigest()[:8]
    content_hash = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return f"{path.stem}_{path_hash}_{content_hash}"
