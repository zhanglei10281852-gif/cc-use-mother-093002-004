"""职业标准与能力证据的基础契约。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StandardVersion:
    entity_id: str
    display_name: str
    revision: int

    def __post_init__(self) -> None:
        if not self.entity_id or not self.display_name or self.revision < 1:
            raise ValueError("版本化实体信息不合法")

    def key(self) -> tuple[str, int]:
        """版本键：同一标准实体按修订号区分。"""
        return (self.entity_id, self.revision)

    def same_lineage(self, other: StandardVersion) -> bool:
        """是否同一标准实体（忽略修订号）。"""
        return isinstance(other, StandardVersion) and self.entity_id == other.entity_id


@dataclass(frozen=True)
class EvidenceRecord:
    record_id: str
    entity_id: str
    category: str

    def __post_init__(self) -> None:
        if not self.record_id or not self.entity_id or not self.category:
            raise ValueError("关联记录信息不完整")
