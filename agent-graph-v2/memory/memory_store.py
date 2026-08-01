"""Memory store with TF-IDF based retrieval.

Stores records as {id, key, value, tags, timestamp} and retrieves
top-K most relevant records for a query using cosine similarity
on TF-IDF vectors.
"""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class MemoryRecord:
    id: str
    key: str
    value: str
    tags: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)


class MemoryStore:
    """In-memory key-value store with TF-IDF retrieval."""

    def __init__(self):
        self._records: Dict[str, MemoryRecord] = {}
        self._documents: List[str] = []
        self._idf: Dict[str, float] = {}
        self._tfidf_matrix: List[Dict[str, float]] = []

    def add(self, record: MemoryRecord) -> None:
        self._records[record.id] = record
        self._invalidate_index()

    def add_many(self, records: List[MemoryRecord]) -> None:
        for r in records:
            self._records[r.id] = r
        self._invalidate_index()

    def get(self, record_id: str) -> Optional[MemoryRecord]:
        return self._records.get(record_id)

    def list_all(self) -> List[MemoryRecord]:
        return list(self._records.values())

    def clear(self) -> None:
        self._records.clear()
        self._invalidate_index()

    def seed_from_workspace(self, workspace_path, tool_executor) -> List[MemoryRecord]:
        """Seed memory from existing workspace files."""
        import os
        records = []
        for root, dirs, files in os.walk(workspace_path):
            for fname in files:
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, workspace_path)
                try:
                    with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                    records.append(MemoryRecord(
                        id=f"file_{rel.replace('/', '_')}",
                        key=f"file: {rel}",
                        value=content,
                        tags=["file", rel.split('.')[-1] if '.' in rel else 'txt'],
                    ))
                except Exception:
                    pass
        self.add_many(records)
        return records

    def retrieve(self, query: str, top_k: int = 5) -> List[Tuple[MemoryRecord, float]]:
        """Retrieve top-K records relevant to the query."""
        if not self._records:
            return []

        self._ensure_index()
        query_vec = self._tfidf_vector(query)

        scored = []
        for i, doc_vec in enumerate(self._tfidf_matrix):
            sim = self._cosine_sim(query_vec, doc_vec)
            if sim > 0:
                scored.append((list(self._records.values())[i], sim))

        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]

    def retrieve_context(self, query: str, top_k: int = 5) -> str:
        """Get retrieved records formatted as context string."""
        results = self.retrieve(query, top_k)
        if not results:
            return ""
        lines = ["[Retrieved memories]:"]
        for rec, score in results:
            lines.append(f"- {rec.key}: {rec.value[:200]}")
        return "\n".join(lines)

    def _invalidate_index(self) -> None:
        self._tfidf_matrix = []
        self._idf = {}

    def _ensure_index(self) -> None:
        if self._tfidf_matrix:
            return
        self._documents = [r.key + " " + r.value for r in self._records.values()]
        self._compute_idf()
        self._tfidf_matrix = [self._tfidf_vector(doc) for doc in self._documents]

    def _compute_idf(self) -> None:
        n = len(self._documents)
        if n == 0:
            return
        df: Dict[str, int] = {}
        for doc in self._documents:
            tokens = set(self._tokenize(doc))
            for t in tokens:
                df[t] = df.get(t, 0) + 1
        self._idf = {t: math.log(n / (1 + df.get(t, 0))) for t in df}

    def _tfidf_vector(self, text: str) -> Dict[str, float]:
        tokens = self._tokenize(text)
        tf: Dict[str, int] = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
        total = len(tokens) if tokens else 1
        vec: Dict[str, float] = {}
        for t, count in tf.items():
            idf = self._idf.get(t, 0.0)
            vec[t] = (count / total) * idf
        return vec

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        text = text.lower()
        tokens = re.findall(r'[a-z]{2,}', text)
        return tokens

    @staticmethod
    def _cosine_sim(a: Dict[str, float], b: Dict[str, float]) -> float:
        shared = set(a) & set(b)
        if not shared:
            return 0.0
        dot = sum(a[t] * b[t] for t in shared)
        mag_a = math.sqrt(sum(v ** 2 for v in a.values()))
        mag_b = math.sqrt(sum(v ** 2 for v in b.values()))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)
