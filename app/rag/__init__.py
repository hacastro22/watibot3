"""
RAG (Retrieval Augmented Generation) module for the watibot system.

This package provides semantic retrieval of system instruction chunks
to replace the LLM-driven load_additional_modules tool call with
Python-side retrieval, reducing API calls and token usage.

Components:
    - chunker: Parses system_instructions_new.txt into semantic chunks
    - chunk_store: ChromaDB persistent collection management
    - embedder: Generates and stores embeddings via OpenAI text-embedding-3-large
    - retriever: Semantic retrieval at query time
    - always_on_core: Builds the always-on system prompt
"""
