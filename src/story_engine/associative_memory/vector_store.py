from typing import List, Dict, Any, Optional
import chromadb
from src.config.config import config
import uuid

class VectorStore:
    def __init__(self, collection_name: str):
        self.collection_name = collection_name
        self._client = self._initialize_client()
        self._collection = self._client.get_or_create_collection(name=self.collection_name)

    def _initialize_client(self) -> chromadb.Client:
        if config.chromadb_persist_dir:
            return chromadb.PersistentClient(path=config.chromadb_persist_dir)
        return chromadb.Client()

    def add_texts(self, texts: List[str], metadatas: Optional[List[Dict[str, Any]]] = None, ids: Optional[List[str]] = None):
        if ids is None:
            ids = [str(uuid.uuid4()) for _ in range(len(texts))]
        
        self._collection.add(
            documents=texts,
            metadatas=metadatas,
            ids=ids
        )

    def upsert_texts(
        self,
        texts: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        ids: Optional[List[str]] = None,
    ):
        if ids is None:
            ids = [str(uuid.uuid4()) for _ in range(len(texts))]
        self._collection.upsert(
            documents=texts,
            metadatas=metadatas,
            ids=ids,
        )

    def query(self, query_texts: List[str], n_results: int = 5, where: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._collection.query(
            query_texts=query_texts,
            n_results=n_results,
            where=where
        )

    def delete(self, ids: Optional[List[str]] = None, where: Optional[Dict[str, Any]] = None):
        self._collection.delete(ids=ids, where=where)

    def get_records(
        self,
        *,
        where: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        kwargs: Dict[str, Any] = {
            "include": ["documents", "metadatas"],
        }
        if where is not None:
            kwargs["where"] = where
        if limit is not None:
            kwargs["limit"] = int(limit)
        result = self._collection.get(**kwargs)
        ids = result.get("ids") or []
        documents = result.get("documents") or []
        metadatas = result.get("metadatas") or []
        return [
            {
                "id": memory_id,
                "content": documents[index] if index < len(documents) else "",
                "metadata": (
                    metadatas[index]
                    if index < len(metadatas) and metadatas[index]
                    else {}
                ),
            }
            for index, memory_id in enumerate(ids)
        ]
