import re
import hashlib
from typing import List, Dict, Any, Optional
from pydantic import PrivateAttr
from src.story_engine.core.component import Component
from src.story_engine.associative_memory.vector_store import VectorStore
from src.story_engine.associative_memory.retriever import Retriever


def _chroma_safe_collection_name(agent_name: str) -> str:
    """ChromaDB 要求: 仅 [a-zA-Z0-9._-]，且以字母或数字开头和结尾，长度 3–512。"""
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", agent_name)
    safe = safe.strip("._-") or "x"
    if not safe[0].isalnum():
        safe = "a" + safe
    if not safe[-1].isalnum():
        safe = safe + "a"
    if len(safe) < 3:
        safe = safe + "_" + hashlib.sha256(agent_name.encode()).hexdigest()[:12]
    return f"memory_{safe}"[:63]


class Memory(Component):
    """
    Agent Memory Component.
    Connects to the Associative Memory system.
    """
    agent_name: str
    collection_name: Optional[str] = None
    
    _vector_store: Optional[VectorStore] = PrivateAttr(default=None)
    _retriever: Optional[Retriever] = PrivateAttr(default=None)

    def __init__(self, agent_name: str, **data):
        data["agent_name"] = agent_name
        super().__init__(**data)
        
        if self.collection_name is None:
            self.collection_name = _chroma_safe_collection_name(self.agent_name)
            
        self._vector_store = VectorStore(collection_name=self.collection_name)
        self._retriever = Retriever(vector_store=self._vector_store)

    def add_memory(self, content: str, metadata: Optional[Dict[str, Any]] = None):
        if metadata is None:
            metadata = {}
        metadata["agent"] = self.agent_name
        self._vector_store.add_texts(texts=[content], metadatas=[metadata])

    def retrieve(self, query: str, n_results: int = 5) -> List[str]:
        return self._retriever.retrieve(query, n_results=n_results)

    def retrieve_detailed(self, query: str, n_results: int = 5) -> List[Dict[str, Any]]:
        return self._retriever.retrieve_with_metadata(query, n_results=n_results)
