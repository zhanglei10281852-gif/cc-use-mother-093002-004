"""企业岗位目录。

岗位按"岗位族 + revision"版本化，并锚定到某个标准版本与等级口径。
人力部门做历史时点查询时，只会使用该日期有效的岗位定义。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ValidationError


@dataclass(frozen=True)
class PositionDefinition:
    position_id: str
    revision: int
    title: str
    standard_key: tuple[str, int]
    grade_label: str
    required_units: frozenset[str] = field(default_factory=frozenset)
    valid_from: str = ""
    valid_to: str | None = None

    def __post_init__(self) -> None:
        if not self.position_id or self.revision < 1 or not self.title:
            raise ValidationError("岗位定义信息不完整")
        if not self.valid_from:
            raise ValidationError("岗位必须有生效日期")
        if not self.required_units:
            raise ValidationError("岗位必须声明至少一项能力单元要求")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValidationError("岗位失效日期不能早于生效日期")

    @property
    def key(self) -> tuple[str, int]:
        return (self.position_id, self.revision)

    def is_effective_on(self, day: str) -> bool:
        if day < self.valid_from:
            return False
        return self.valid_to is None or day < self.valid_to


class PositionRegistry:
    def __init__(self) -> None:
        self._positions: dict[tuple[str, int], PositionDefinition] = {}

    def publish(self, position: PositionDefinition) -> PositionDefinition:
        if position.key in self._positions:
            raise ValidationError(f"岗位定义已存在: {position.key}")
        self._positions[position.key] = position
        return position

    def get(self, position_id: str, revision: int) -> PositionDefinition:
        try:
            return self._positions[(position_id, revision)]
        except KeyError:
            raise ValidationError(f"岗位定义不存在: {(position_id, revision)}") from None

    def effective_on(self, day: str) -> list[PositionDefinition]:
        return [p for p in self._positions.values() if p.is_effective_on(day)]

    def for_standard_on(
        self, standard_key: tuple[str, int], day: str
    ) -> list[PositionDefinition]:
        return [
            p
            for p in self.effective_on(day)
            if p.standard_key == standard_key
        ]
