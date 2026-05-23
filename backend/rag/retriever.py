"""
RAG retriever — semantic search over the ChromaDB collection.
"""
import logging
from pathlib import Path

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

logger = logging.getLogger("jarvis.rag.retriever")


class Retriever:
    def __init__(self, config: dict, chroma_path: Path, embedder=None, chroma_client=None):
        self.collection_name: str = config.get("collection_name", "jarvis_knowledge")
        self.top_k: int = config.get("top_k", 3)
        self.similarity_threshold: float = config.get("similarity_threshold", 0.4)

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def retrieve(self, query: str) -> list[dict]:
        """
        Return up to *top_k* chunks whose cosine similarity exceeds *threshold*.

        Each result dict has:
          text     — the chunk text
          score    — similarity score  (0..1, higher = more similar)
          source   — file name
        """
        col_count = self._col.count()
        if col_count == 0:
            return []

        query_embedding = self._embedder.encode([query], show_progress_bar=False).tolist()

        results = self._col.query(
            query_embeddings=query_embedding,
            n_results=min(self.top_k, col_count),
            include=["documents", "distances", "metadatas"],
        )

        chunks: list[dict] = []
        for doc, dist, meta in zip(
            results["documents"][0],
            results["distances"][0],
            results["metadatas"][0],
        ):
            # ChromaDB cosine distance → similarity: sim = 1 - dist
            similarity = 1.0 - dist
            if similarity >= self.similarity_threshold:
                chunks.append(
                    {
                        "text": doc,
                        "score": round(similarity, 4),
                        "source": meta.get("file_name", "unknown"),
                    }
                )

        logger.debug(
            "RAG retrieved %d/%d chunks above threshold %.2f for query %r",
            len(chunks),
            self.top_k,
            self.similarity_threshold,
            query[:60],
        )
        return chunks

    def build_context(self, query: str) -> tuple[str, bool]:
        """
        Return (context_text, used_rag) where context_text is ready to
        inject into the LLM prompt.  used_rag is False when no good chunks
        were found (caller should fall back to kiwix).
        """
        chunks = self.retrieve(query)
        if not chunks:
            return "", False
        parts = [f"[Source: {c['source']} | score: {c['score']}]\n{c['text']}" for c in chunks]
        return "\n\n".join(parts), True
