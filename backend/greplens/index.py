"""Document index: LlamaIndex nodes with location metadata, stored in Chroma, embedded by BGE-M3."""
from __future__ import annotations

import re
from functools import lru_cache

import chromadb
from chromadb.config import Settings as ChromaSettings
from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.schema import TextNode
from llama_index.core.vector_stores import ExactMatchFilter, MetadataFilters
from llama_index.vector_stores.chroma import ChromaVectorStore

from . import llm
from .config import data_dir

CHUNK_CHARS = 1400


class EngineEmbedding(BaseEmbedding):
    """BGE-M3 served by the local engine. BGE-M3 needs no query instruction."""

    def _get_query_embedding(self, query: str) -> list[float]:
        return llm.embed([query])[0]

    def _get_text_embedding(self, text: str) -> list[float]:
        return llm.embed([text])[0]

    def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        return llm.embed(texts)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._get_query_embedding(query)


@lru_cache(maxsize=1)
def _client():
    return chromadb.PersistentClient(path=str(data_dir() / "index"),
                                     settings=ChromaSettings(anonymized_telemetry=False))


def _store(workspace_id: str) -> ChromaVectorStore:
    collection = _client().get_or_create_collection(f"ws_{workspace_id}", metadata={"hnsw:space": "cosine"})
    return ChromaVectorStore(chroma_collection=collection)


def build_chunks(segments: list[dict]) -> list[dict]:
    """Merge short neighbouring segments on the same page and article into retrieval chunks."""
    chunks, current = [], None
    for seg in segments:
        same = current and current["page"] == seg["page"] and current["article"] == (seg["article"] or "")
        if same and len(current["text"]) + len(seg["text"]) < CHUNK_CHARS:
            current["text"] += "\n" + seg["text"]
            current["segment_ids"].append(seg["id"])
            continue
        current = {"text": seg["text"], "page": seg["page"] or 0, "article": seg["article"] or "",
                   "location": seg["location"], "segment_ids": [seg["id"]]}
        chunks.append(current)
    return chunks


def index_document(workspace_id: str, document_id: str, segments: list[dict], progress=None) -> int:
    chunks = build_chunks(segments)
    nodes = [TextNode(text=c["text"], id_=f"{document_id}-{i}", metadata={
        "gl_doc": document_id, "page": c["page"], "location": c["location"],
        "segment_ids": ",".join(c["segment_ids"])},
        excluded_embed_metadata_keys=["gl_doc", "segment_ids", "page"],
        excluded_llm_metadata_keys=["gl_doc", "segment_ids"]) for i, c in enumerate(chunks)]
    storage = StorageContext.from_defaults(vector_store=_store(workspace_id))
    embed = EngineEmbedding(embed_batch_size=16)
    for start in range(0, len(nodes), 64):
        VectorStoreIndex(nodes[start:start + 64], storage_context=storage, embed_model=embed)
        if progress:
            progress(min(len(nodes), start + 64), len(nodes))
    return len(nodes)


def delete_document(workspace_id: str, document_id: str) -> None:
    try:
        _store(workspace_id)._collection.delete(where={"gl_doc": document_id})
    except Exception:
        pass


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[\w؀-ۿ]{3,}", text.lower())}


def retrieve(workspace_id: str, document_id: str, query: str, k: int = 6) -> list[dict]:
    index = VectorStoreIndex.from_vector_store(_store(workspace_id), embed_model=EngineEmbedding())
    retriever = index.as_retriever(similarity_top_k=k * 3, filters=MetadataFilters(
        filters=[ExactMatchFilter(key="gl_doc", value=document_id)]))
    results = retriever.retrieve(query)
    q_terms = _terms(query)
    scored = []
    for hit in results:
        lexical = len(q_terms & _terms(hit.node.text)) / max(1, len(q_terms))
        scored.append((0.7 * (hit.score or 0) + 0.3 * lexical, hit))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [{"text": hit.node.text, "location": hit.node.metadata.get("location", ""),
             "page": hit.node.metadata.get("page", 0),
             "segment_ids": hit.node.metadata.get("segment_ids", "").split(","), "score": round(score, 3)}
            for score, hit in scored[:k]]
