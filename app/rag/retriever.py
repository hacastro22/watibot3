"""
Retriever module for semantic chunk retrieval at query time.

Embeds the user message (+ optional conversation context), queries ChromaDB
for the top-K most relevant chunks, and returns formatted content ready
for injection into the system prompt.
"""

import logging
from typing import List, Dict, Any, Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# Default number of chunks to retrieve per query
# Set to 12 to ensure critical protocol chunks (e.g. quote_generation_protocol,
# pricing_logic) are captured even for ambiguous pricing queries where they
# may rank between #6 and #12 depending on phrasing.
DEFAULT_TOP_K = 12

# Maximum total characters of retrieved content to inject
MAX_RETRIEVED_CHARS = 20000


async def retrieve(
    user_message: str,
    conversation_context: str = "",
    top_k: int = DEFAULT_TOP_K,
    openai_client: Optional[AsyncOpenAI] = None,
) -> str:
    """Retrieve relevant system instruction chunks for a user message.

    Embeds the user query combined with recent conversation context,
    queries ChromaDB for semantically similar chunks, and returns
    the chunk contents formatted for injection into the system prompt.

    Args:
        user_message: The current user message.
        conversation_context: Optional recent conversation messages
                              for context-aware retrieval.
        top_k: Number of top chunks to retrieve.
        openai_client: Optional pre-existing AsyncOpenAI client.

    Returns:
        Formatted string with retrieved chunk contents, ready for
        injection into the system prompt.
    """
    from .embedder import embed_query
    from .chunk_store import query_chunks

    # Build the query: user message + conversation context for better retrieval
    query_text = user_message
    if conversation_context:
        query_text = f"{conversation_context}\n\nCurrent message: {user_message}"

    # Embed the query
    query_embedding = await embed_query(query_text, client=openai_client)

    # Query ChromaDB for top-K chunks
    results = query_chunks(query_embedding, n_results=top_k)

    if not results:
        logger.warning("[RETRIEVER] No chunks retrieved for query")
        return ""

    # Format results for injection into system prompt
    formatted = _format_retrieved_chunks(results)

    total_chars = sum(r["char_count"] for r in results)
    logger.info(
        f"[RETRIEVER] Retrieved {len(results)} chunks ({total_chars:,} chars) "
        f"for query: {user_message[:80]}..."
    )

    return formatted


async def retrieve_with_details(
    user_message: str,
    conversation_context: str = "",
    top_k: int = DEFAULT_TOP_K,
    openai_client: Optional[AsyncOpenAI] = None,
) -> Dict[str, Any]:
    """Retrieve chunks with full details (for debugging/logging).

    Same as retrieve() but returns structured data instead of formatted text.

    Args:
        user_message: The current user message.
        conversation_context: Optional recent conversation context.
        top_k: Number of top chunks to retrieve.
        openai_client: Optional pre-existing AsyncOpenAI client.

    Returns:
        Dict with keys: formatted_content, chunks (list of result dicts),
        query_text, total_chars.
    """
    from .embedder import embed_query
    from .chunk_store import query_chunks

    query_text = user_message
    if conversation_context:
        query_text = f"{conversation_context}\n\nCurrent message: {user_message}"

    query_embedding = await embed_query(query_text, client=openai_client)
    results = query_chunks(query_embedding, n_results=top_k)

    formatted = _format_retrieved_chunks(results) if results else ""
    total_chars = sum(r["char_count"] for r in results)

    return {
        "formatted_content": formatted,
        "chunks": results,
        "query_text": query_text,
        "total_chars": total_chars,
    }


def _format_retrieved_chunks(results: List[Dict[str, Any]]) -> str:
    """Format retrieved chunks into a string for the system prompt.

    Includes module context and the exact original JSON content.
    Respects MAX_RETRIEVED_CHARS to avoid bloating the prompt.

    Args:
        results: List of chunk result dicts from chunk_store.query_chunks().

    Returns:
        Formatted string with all retrieved chunk contents.
    """
    parts = []
    total_chars = 0

    for r in results:
        chunk_content = r["content"]

        # Respect character limit
        if total_chars + len(chunk_content) > MAX_RETRIEVED_CHARS:
            logger.info(
                f"[RETRIEVER] Reached char limit ({MAX_RETRIEVED_CHARS}), "
                f"stopping at {len(parts)} chunks"
            )
            break

        parts.append(
            f"=== {r['chunk_id']} (relevance: {1 - r['distance']:.2f}) ===\n"
            f"{chunk_content}"
        )
        total_chars += len(chunk_content)

    return "\n\n".join(parts)
