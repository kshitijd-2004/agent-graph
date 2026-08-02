"""Dense retriever wrapper for memory records.

Wraps MemoryStore and provides the retrieval interface used by
the agent loop. Falls back to TF-IDF if sentence-transformers
is not available.
"""

from typing import Dict, List, Optional, Tuple

from memory.memory_store import MemoryStore, MemoryRecord


class MemoryRetriever:
    """Retrieves relevant memory records for agent queries."""

    def __init__(self, store: MemoryStore, top_k: int = 5):
        self.store = store
        self.top_k = top_k

    def retrieve(self, query: str, top_k: Optional[int] = None) -> List[Tuple[MemoryRecord, float]]:
        k = top_k or self.top_k
        return self.store.retrieve(query, k)

    def get_context(self, query: str, top_k: Optional[int] = None) -> str:
        return self.store.retrieve_context(query, top_k or self.top_k)
