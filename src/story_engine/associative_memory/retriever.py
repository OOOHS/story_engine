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
