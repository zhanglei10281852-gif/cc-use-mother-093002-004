"""证书版本与标准版本之间的互认映射。

每条映射由评审案件产生，给出一种互认决定：

* EQUIVALENT 等同：源证书版本对目标标准要求全部覆盖；
* PARTIAL_EQUIVALENT 部分等同：覆盖部分能力单元，其余列为缺口；
* ADDITIONAL_ASSESSMENT 附加考核：通过附加考核后才认可指定单元；
* NOT_RECOGNIZED 不予互认。

映射只追加、不删除。标准升级或重新评审时，旧映射通过置位 valid_to
变为 SUPERSEDED 状态保留下来（历史招聘依据可追溯），新映射另行生效，
已生效结论不会被平行修改覆盖。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .errors import ValidationError


class MappingDecision(str, Enum):
    EQUIVALENT = "等同"
    PARTIAL_EQUIVALENT = "部分等同"
    ADDITIONAL_ASSESSMENT = "附加考核"
    NOT_RECOGNIZED = "不予互认"


class MappingStatus(str, Enum):
    EFFECTIVE = "生效"
    SUPERSEDED = "已被替代"
    WITHDRAWN = "已撤回"


@dataclass(frozen=True)
class RecognitionMapping:
    mapping_id: str
    source_cert_key: tuple[str, int]
    target_standard_key: tuple[str, int]
    target_grade_label: str
    decision: MappingDecision
    covered_units: frozenset[str] = field(default_factory=frozenset)
    assessment_units: frozenset[str] = field(default_factory=frozenset)
    valid_from: str = ""
    valid_to: str | None = None
    status: MappingStatus = MappingStatus.EFFECTIVE
    source_case_id: str = ""
    basis: str = ""
    supersedes: str = ""  # 升级延续时指向被替代的旧映射 id
    superseded_by: str = ""  # 被哪条新映射替代（升级后可追溯新结论）

    def __post_init__(self) -> None:
        if not self.mapping_id or not self.source_case_id:
            raise ValidationError("映射必须有标识与来源案件")
        if not self.valid_from:
            raise ValidationError("映射必须有生效日期")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValidationError("映射失效日期不能早于生效日期")
        overlap = self.covered_units & self.assessment_units
        if overlap:
            raise ValidationError(f"同一能力单元不能既直接认可又要求附加考核: {sorted(overlap)}")

    def is_effective_on(self, day: str) -> bool:
        """该映射在指定日期是否处于其生效区间内（含生效日，不含失效日）。

        被替代/撤回的映射在其历史区间内仍返回真——这正是历史招聘依据
        可复原的关键；状态字段只说明它为何结束，不改变区间事实。
        """
        if day < self.valid_from:
            return False
        return self.valid_to is None or day < self.valid_to

    def is_current(self) -> bool:
        """是否为当前仍可用于新结论的映射（未被替代或撤回）。"""
        return self.status is MappingStatus.EFFECTIVE

    @property
    def accepted_units(self) -> frozenset[str]:
        """无需额外条件即可认可的单元（附加考核单元在通过考核前不算）。"""
        return frozenset(self.covered_units)


def validate_mapping_against_versions(
    mapping: RecognitionMapping,
    cert_covered_units: frozenset[str],
    target_units: frozenset[str],
) -> None:
    """依据源证书版本实际覆盖与目标标准全部单元校验映射口径。"""
    declared = mapping.covered_units | mapping.assessment_units
    unknown = declared - target_units
    if unknown:
        raise ValidationError(f"映射引用了目标标准中不存在的能力单元: {sorted(unknown)}")
    beyond_source = mapping.covered_units - cert_covered_units
    if beyond_source:
        raise ValidationError(f"源证书版本未覆盖的单元不能判为等同: {sorted(beyond_source)}")
    if mapping.decision is MappingDecision.EQUIVALENT:
        if mapping.covered_units != target_units or mapping.assessment_units:
            raise ValidationError("等同决定必须覆盖目标标准的全部能力单元")
    elif mapping.decision is MappingDecision.PARTIAL_EQUIVALENT:
        if not mapping.covered_units or mapping.covered_units >= target_units:
            raise ValidationError("部分等同决定必须只覆盖目标标准的部分单元")
        if mapping.assessment_units:
            raise ValidationError("部分等同不应夹带附加考核单元，请使用附加考核决定")
    elif mapping.decision is MappingDecision.ADDITIONAL_ASSESSMENT:
        if not mapping.assessment_units:
            raise ValidationError("附加考核决定必须列明需考核的能力单元")
    elif mapping.decision is MappingDecision.NOT_RECOGNIZED:
        if declared:
            raise ValidationError("不予互认决定不得声明任何认可单元")


class MappingBook:
    """映射台账：追加保存全部历史映射，按证书版本与标准版本检索。"""

    def __init__(self) -> None:
        self._mappings: dict[str, RecognitionMapping] = {}

    def add(self, mapping: RecognitionMapping) -> RecognitionMapping:
        if mapping.mapping_id in self._mappings:
            raise ValidationError(f"映射已存在，不能重复登记: {mapping.mapping_id}")
        self._mappings[mapping.mapping_id] = mapping
        return mapping

    def terminate(
        self,
        mapping_id: str,
        valid_to: str,
        status: MappingStatus = MappingStatus.SUPERSEDED,
        superseded_by_new: str = "",
    ) -> RecognitionMapping:
        """让一条映射在 valid_to 起失效（升级替代或撤回）。

        历史可查性不受影响：该映射在 valid_from..valid_to 区间内
        ``is_effective_on`` 仍为真，历史时点的招聘依据不会被改写。
        不允许对已经失效的映射再次操作（并行保护）。
        """
        from dataclasses import replace

        current = self.get(mapping_id)
        if current.status is not MappingStatus.EFFECTIVE:
            raise ValidationError(f"映射已非生效状态，不能重复处置: {mapping_id}")
        if valid_to < current.valid_from:
            raise ValidationError("失效日期不能早于生效日期")
        updated = replace(current, valid_to=valid_to, status=status, superseded_by=superseded_by_new)
        self._mappings[mapping_id] = updated
        return updated

    def get(self, mapping_id: str) -> RecognitionMapping:
        try:
            return self._mappings[mapping_id]
        except KeyError:
            raise ValidationError(f"映射不存在: {mapping_id}") from None

    def exists(self, mapping_id: str) -> bool:
        return mapping_id in self._mappings

    def all(self) -> list[RecognitionMapping]:
        return list(self._mappings.values())

    def effective_on(self, day: str) -> list[RecognitionMapping]:
        return [m for m in self._mappings.values() if m.is_effective_on(day)]

    def for_source_on(self, cert_key: tuple[str, int], day: str) -> list[RecognitionMapping]:
        return [
            m
            for m in self._mappings.values()
            if m.source_cert_key == cert_key and m.is_effective_on(day)
        ]

    def effective_rules(
        self,
        cert_key: tuple[str, int],
        target_standard_key: tuple[str, int],
        grade_label: str,
        day: str,
        exclude_case_id: str = "",
    ) -> list[RecognitionMapping]:
        """同一证书版本 → 同一标准版本+等级口径，在 day 已生效的规则。

        exclude_case_id 用于排除本案件自身的旧映射（复议改判场景）。
        """
        return [
            m
            for m in self._mappings.values()
            if m.source_cert_key == cert_key
            and m.target_standard_key == target_standard_key
            and m.target_grade_label == grade_label
            and m.source_case_id != exclude_case_id
            and m.is_effective_on(day)
            and m.is_current()
        ]

    def superseded_copies_kept(self) -> int:
        """被替代但仍保留的历史映射数量（便于审计）。"""
        return sum(
            1 for m in self._mappings.values() if m.status is not MappingStatus.EFFECTIVE
        )
