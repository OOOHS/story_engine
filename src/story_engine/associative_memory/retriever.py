from typing import List, Dict, Any
from src.story_engine.associative_memory.vector_store import VectorStore

class Retriever:
    def __init__(self, vector_store: VectorStore):
        self.vector_store = vector_store

    def retrieve(self, query: str, n_results: int = 5, where: Dict[str, Any] = None) -> List[str]:
        """
        Simple retrieval that returns a list of document contents.
        """
        results = self.vector_store.query(query_texts=[query], n_results=n_results, where=where)
        documents = []
        if results['documents']:
            # results['documents'] is a list of lists (one list per query)
            documents = results['documents'][0]
        return documents

    def retrieve_with_metadata(self, query: str, n_results: int = 5, where: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Returns documents with their metadata.
        """
        results = self.vector_store.query(query_texts=[query], n_results=n_results, where=where)
        items = []
        if results['documents']:
            docs = results['documents'][0]
            metas = results['metadatas'][0] if results['metadatas'] else [{}] * len(docs)
            for doc, meta in zip(docs, metas):
                items.append({"content": doc, "metadata": meta})
        return items

    def retrieve_many(
        self,
        queries: List[str],
        n_results: int = 5,
        where: Dict[str, Any] = None,
    ) -> List[List[str]]:
        if not queries:
            return []
        results = self.vector_store.query(
            query_texts=list(queries),
            n_results=n_results,
            where=where,
        )
        documents = results.get("documents") or []
        return [list(items or []) for items in documents]

    def retrieve_many_with_metadata(
        self,
        queries: List[str],
        n_results: int = 5,
        where: Dict[str, Any] = None,
    ) -> List[List[Dict[str, Any]]]:
        if not queries:
            return []
        results = self.vector_store.query(
            query_texts=list(queries),
            n_results=n_results,
            where=where,
        )
        documents = results.get("documents") or []
        metadatas = results.get("metadatas") or []
        distances = results.get("distances") or []
        batches = []
        for index, docs in enumerate(documents):
            metas = metadatas[index] if index < len(metadatas) else []
            dists = distances[index] if index < len(distances) else []
            batches.append(
                [
                    {
                        "content": doc,
                        "metadata": metas[item_index]
                        if item_index < len(metas) and metas[item_index]
                        else {},
                        "distance": dists[item_index]
                        if item_index < len(dists)
                        else None,
                    }
                    for item_index, doc in enumerate(docs or [])
                ]
            )
        return batches
