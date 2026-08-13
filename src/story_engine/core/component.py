from typing import Any, ClassVar, Optional
from pydantic import BaseModel, PrivateAttr

class Component(BaseModel):
    component_slot: ClassVar[str | None] = None
    _entity: Optional[Any] = PrivateAttr(default=None)

    @property
    def entity(self) -> Optional[Any]:
        return self._entity

    @entity.setter
    def entity(self, value: Any):
        self._entity = value
