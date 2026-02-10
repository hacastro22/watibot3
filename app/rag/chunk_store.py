"""
ChromaDB persistent collection management for RAG chunks.

Manages the vector database that stores embeddings of system instruction chunks.
Provides methods to initialize, add, query, and rebuild the collection.
"""

import logging
import os
import sys
from typing import Dict, List, Any, Optional

# Monkey-patch sqlite3 with pysqlite3-binary to satisfy ChromaDB's
# requirement for sqlite3 >= 3.35.0 (system has 3.31.1)
try:
    __import__("pysqlite3")
    sys.modules["sqlite3"] = sys.modules.pop("pysqlite3")
except ImportError:
    pass

logger = logging.getLogger(__name__)

# Default path for ChromaDB persistent storage
DEFAULT_PERSIST_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "rag",
    "chroma_db",
)

COLLECTION_NAME = "system_instruction_chunks"
EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIMENSIONS = 3072


def get_chroma_client(persist_dir: str = None):
    """Get a persistent ChromaDB client.

    Args:
        persist_dir: Directory for persistent storage.
                     Defaults to app/rag/chroma_db/.

    Returns:
        ChromaDB PersistentClient instance.
    """
    import chromadb

    persist_dir = persist_dir or DEFAULT_PERSIST_DIR
    os.makedirs(persist_dir, exist_ok=True)
    return chromadb.PersistentClient(path=persist_dir)


def get_or_create_collection(client=None, persist_dir: str = None):
    """Get or create the system instruction chunks collection.

    Uses cosine similarity as the distance function, which works
    well with OpenAI embeddings (they are normalized).

    Args:
        client: Optional pre-existing ChromaDB client.
        persist_dir: Directory for persistent storage.

    Returns:
        ChromaDB Collection instance.
    """
    if client is None:
        client = get_chroma_client(persist_dir)

    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def add_chunks(
    chunks: List[Dict[str, Any]],
    embeddings: List[List[float]],
    collection=None,
    persist_dir: str = None,
) -> int:
    """Add chunks with their embeddings to the collection.

    Args:
        chunks: List of chunk dicts from chunker.chunk_modules().
        embeddings: List of embedding vectors, one per chunk.
        collection: Optional pre-existing collection.
        persist_dir: Directory for persistent storage.

    Returns:
        Number of chunks added.
    """
    if collection is None:
        collection = get_or_create_collection(persist_dir=persist_dir)

    ids = [c["chunk_id"] for c in chunks]
    documents = [c["content"] for c in chunks]
    metadatas = [
        {
            "module_name": c["module_name"],
            "section": c["section"],
            "char_count": c["char_count"],
            "content_for_embedding": c["content_for_embedding"],
        }
        for c in chunks
    ]

    collection.add(
        ids=ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )

    logger.info(f"[CHUNK_STORE] Added {len(ids)} chunks to collection '{COLLECTION_NAME}'")
    return len(ids)


def query_chunks(
    query_embedding: List[float],
    n_results: int = 8,
    collection=None,
    persist_dir: str = None,
) -> List[Dict[str, Any]]:
    """Query the collection for the most relevant chunks.

    Args:
        query_embedding: Embedding vector of the user query.
        n_results: Number of top results to return.
        collection: Optional pre-existing collection.
        persist_dir: Directory for persistent storage.

    Returns:
        List of result dicts with keys: chunk_id, content, module_name,
        section, distance, char_count.
    """
    if collection is None:
        collection = get_or_create_collection(persist_dir=persist_dir)

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )

    parsed = []
    if results and results["ids"] and results["ids"][0]:
        for i, chunk_id in enumerate(results["ids"][0]):
            parsed.append({
                "chunk_id": chunk_id,
                "content": results["documents"][0][i],
                "module_name": results["metadatas"][0][i]["module_name"],
                "section": results["metadatas"][0][i]["section"],
                "distance": results["distances"][0][i],
                "char_count": results["metadatas"][0][i]["char_count"],
            })

    return parsed


def rebuild_collection(persist_dir: str = None) -> None:
    """Delete and recreate the collection (for re-indexing).

    Args:
        persist_dir: Directory for persistent storage.
    """
    client = get_chroma_client(persist_dir)
    try:
        client.delete_collection(COLLECTION_NAME)
        logger.info(f"[CHUNK_STORE] Deleted existing collection '{COLLECTION_NAME}'")
    except Exception:
        pass

    get_or_create_collection(client=client)
    logger.info(f"[CHUNK_STORE] Created fresh collection '{COLLECTION_NAME}'")


def get_collection_info(persist_dir: str = None) -> Dict[str, Any]:
    """Get information about the current collection.

    Args:
        persist_dir: Directory for persistent storage.

    Returns:
        Dict with collection name, count, and metadata.
    """
    try:
        collection = get_or_create_collection(persist_dir=persist_dir)
        return {
            "name": COLLECTION_NAME,
            "count": collection.count(),
            "metadata": collection.metadata,
        }
    except Exception as e:
        return {"name": COLLECTION_NAME, "count": 0, "error": str(e)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    info = get_collection_info()
    print(f"Collection: {info['name']}")
    print(f"Chunks stored: {info['count']}")
    if "error" in info:
        print(f"Error: {info['error']}")
