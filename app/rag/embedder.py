"""
Embedder module for generating and storing OpenAI embeddings.

Uses text-embedding-3-large to embed system instruction chunks
and stores them in ChromaDB via the chunk_store module.
Can also embed individual queries at retrieval time.
"""

import asyncio
import logging
import os
from typing import List, Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIMENSIONS = 3072


def _get_openai_client() -> AsyncOpenAI:
    """Get an AsyncOpenAI client using the configured API key.

    Returns:
        AsyncOpenAI client instance.

    Raises:
        ValueError: If OPENAI_API_KEY is not set.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        try:
            from app import config
            api_key = config.OPENAI_API_KEY
        except (ImportError, AttributeError):
            pass

    if not api_key:
        raise ValueError("OPENAI_API_KEY not found in environment or app.config")

    return AsyncOpenAI(api_key=api_key)


async def embed_texts(
    texts: List[str],
    client: Optional[AsyncOpenAI] = None,
) -> List[List[float]]:
    """Generate embeddings for a list of texts using OpenAI API.

    Args:
        texts: List of text strings to embed.
        client: Optional pre-existing AsyncOpenAI client.

    Returns:
        List of embedding vectors (each a list of floats).
    """
    if client is None:
        client = _get_openai_client()

    # OpenAI supports batching up to 2048 inputs per request
    # Our ~77 chunks fit in a single batch
    response = await client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=texts,
        dimensions=EMBEDDING_DIMENSIONS,
    )

    embeddings = [item.embedding for item in response.data]
    logger.info(
        f"[EMBEDDER] Generated {len(embeddings)} embeddings "
        f"({EMBEDDING_MODEL}, {EMBEDDING_DIMENSIONS}d), "
        f"usage: {response.usage.total_tokens} tokens"
    )
    return embeddings


async def embed_query(
    query: str,
    client: Optional[AsyncOpenAI] = None,
) -> List[float]:
    """Generate an embedding for a single query string.

    Args:
        query: The query text to embed.
        client: Optional pre-existing AsyncOpenAI client.

    Returns:
        Embedding vector (list of floats).
    """
    if client is None:
        client = _get_openai_client()

    response = await client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=query,
        dimensions=EMBEDDING_DIMENSIONS,
    )

    return response.data[0].embedding


async def index_all_chunks(rebuild: bool = True) -> int:
    """Parse system instructions, embed all chunks, and store in ChromaDB.

    This is the main one-time indexing function. Run it whenever
    system_instructions_new.txt changes.

    Args:
        rebuild: If True, delete existing collection before indexing.

    Returns:
        Number of chunks indexed.
    """
    from .chunker import chunk_modules
    from .chunk_store import rebuild_collection, add_chunks, get_or_create_collection

    logger.info("[EMBEDDER] Starting full chunk indexing...")

    # Parse chunks
    chunks = chunk_modules()
    if not chunks:
        logger.error("[EMBEDDER] No chunks produced by chunker")
        return 0

    # Rebuild collection if requested
    if rebuild:
        rebuild_collection()

    # Generate embeddings using content_for_embedding (natural language)
    texts_to_embed = [c["content_for_embedding"] for c in chunks]
    embeddings = await embed_texts(texts_to_embed)

    # Store in ChromaDB
    collection = get_or_create_collection()
    count = add_chunks(chunks, embeddings, collection=collection)

    logger.info(f"[EMBEDDER] Indexing complete: {count} chunks stored")
    return count


if __name__ == "__main__":
    # Standalone script: run full indexing
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    print("=== RAG Chunk Indexer ===")
    print(f"Model: {EMBEDDING_MODEL} ({EMBEDDING_DIMENSIONS} dimensions)")
    print()

    count = asyncio.run(index_all_chunks(rebuild=True))

    print(f"\nDone! Indexed {count} chunks.")

    # Verify
    from .chunk_store import get_collection_info
    info = get_collection_info()
    print(f"Collection '{info['name']}' now has {info['count']} chunks stored.")
