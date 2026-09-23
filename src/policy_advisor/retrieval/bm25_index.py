"""Sparse keyword index over the same chunks as the vector store, for
exact rule/order-number matches that dense embeddings handle unreliably
(docs/03). Built in-memory from one matter's chunks.json sidecar - cheap
enough at this corpus size to not need persistence.

Per-matter, not global: BM25 has no native metadata filter the way Chroma
does, and its IDF statistics shouldn't be computed across unrelated matters'
documents anyway - each matter gets its own index built from just its own
chunks (CLAUDE-2.md capability 1)."""

from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from policy_advisor.ingestion.matter_store import load_matter_chunks


@dataclass
class BM25Document:
    metadata: dict
    text: str
    search_text: str = ""  # text + translated_text, so a query in either language can match by keyword


def _tokenize(text: str) -> list[str]:
    return text.lower().split()


class BM25Index:
    def __init__(self, documents: list[BM25Document]):
        self.documents = documents
        self._bm25 = BM25Okapi([_tokenize(doc.search_text or doc.text) for doc in documents]) if documents else None

    def search(self, query: str, top_k: int) -> list[tuple[BM25Document, float]]:
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(_tokenize(query))
        ranked = sorted(zip(self.documents, scores), key=lambda pair: pair[1], reverse=True)
        return ranked[:top_k]


def load_bm25_index(matter_id: str) -> BM25Index:
    raw = load_matter_chunks(matter_id)
    documents = [
        BM25Document(
            metadata={k: v for k, v in item.items() if k != "text"},
            text=item["text"],
            search_text=f"{item['text']} {item.get('translated_text', '')}".strip(),
        )
        for item in raw
    ]
    return BM25Index(documents)
