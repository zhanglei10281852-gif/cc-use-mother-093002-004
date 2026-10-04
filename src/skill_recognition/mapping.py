"""互认映射与标准升级影响分析。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from .catalog import CertificateVersion, HeldCertificate
from .contracts import StandardVersion


class MappingBasis(str, Enum):
    """映射来源类型。"""

    CERTIFICATE = "证书版本"
    COURSE = "课程成果"
    EVIDENCE = "实操证据"


@dataclass(frozen=True)
class EquivalenceMapping:
    """一条互认映射：来源（证书版本/课程/证据类别）到目标标准版本能力单元的对应关系。"""

    mapping_id: str
    basis: MappingBasis
    source_id: str
    standard: StandardVersion
    target_units: tuple[str, ...]
    effective_from: date
    effective_to: date | None = None
    supersedes: str | None = None

    def __post_init__(self) -> None:
        if not self.mapping_id or not self.source_id or not self.target_units:
            raise ValueError("映射信息不完整")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("映射有效期不合法")


class MappingRegistry:
    """映射登记处：映射不可改写，终止与被取代只追加记录。"""

    def __init__(self) -> None:
        self._mappings: dict[str, EquivalenceMapping] = {}
        self._retired_on: dict[str, date] = {}

    def register(self, mapping: EquivalenceMapping) -> None:
        if mapping.mapping_id in self._mappings:
            raise ValueError("映射编号已存在")
        if mapping.supersedes is not None and mapping.supersedes not in self._mappings:
            raise ValueError("被取代的映射不存在")
        self._mappings[mapping.mapping_id] = mapping
        if mapping.supersedes is not None:
            # 旧映射自新映射生效之日起不再生效，历史记录保留
            self._retired_on[mapping.supersedes] = mapping.effective_from

    def get(self, mapping_id: str) -> EquivalenceMapping:
        try:
            return self._mappings[mapping_id]
        except KeyError:
            raise KeyError(f"映射不存在: {mapping_id}") from None

    def retire(self, mapping_id: str, on: date) -> None:
        self.get(mapping_id)
        self._retired_on[mapping_id] = on

    def effective_on(self, day: date) -> tuple[EquivalenceMapping, ...]:
        """某一日实际生效的映射集合。"""
        result = []
        for mapping in self._mappings.values():
            if mapping.effective_from > day:
                continue
            if mapping.effective_to is not None and day > mapping.effective_to:
                continue
            retired = self._retired_on.get(mapping.mapping_id)
            if retired is not None and day >= retired:
                continue
            result.append(mapping)
        return tuple(result)


@dataclass(frozen=True)
class SupplementaryRequirement:
    """标准升级后，持证人需要补充证明的能力单元。"""

    holder_id: str
    cert_id: str
    missing_units: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class UpgradePlan:
    """标准升级的影响分析：哪些映射继续有效、哪些终止、哪些持证人需补证。"""

    old_standard: StandardVersion
    new_standard: StandardVersion
    upgrade_date: date
    continued_mappings: tuple[str, ...]
    terminated_mappings: tuple[str, ...]
    supplementary: tuple[SupplementaryRequirement, ...]


def plan_upgrade(
    old_standard: StandardVersion,
    new_standard: StandardVersion,
    unit_carryover: dict[str, str | None],
    active_mappings: tuple[EquivalenceMapping, ...] | list[EquivalenceMapping],
    held_certificates: tuple[HeldCertificate, ...] | list[HeldCertificate],
    certificate_versions: tuple[CertificateVersion, ...] | list[CertificateVersion],
    upgrade_date: date,
) -> UpgradePlan:
    """计算升级方案，不修改任何既有记录。

    unit_carryover: 旧能力单元 -> 新能力单元；值为 None 表示新标准不再包含该能力。
    """
    if not old_standard.same_lineage(new_standard):
        raise ValueError("标准升级必须针对同一标准实体")
    if new_standard.revision <= old_standard.revision:
        raise ValueError("新标准版本必须高于旧版本")

    continued: list[str] = []
    terminated: list[str] = []
    for mapping in active_mappings:
        if mapping.standard.key() != old_standard.key():
            continue
        if all(unit_carryover.get(unit) for unit in mapping.target_units):
            continued.append(mapping.mapping_id)
        else:
            terminated.append(mapping.mapping_id)

    versions_by_id = {cert.cert_id: cert for cert in certificate_versions}
    supplementary: list[SupplementaryRequirement] = []
    seen: set[tuple[str, str]] = set()
    for held in held_certificates:
        cert = versions_by_id.get(held.cert_id)
        if cert is None or cert.standard.key() != old_standard.key():
            continue
        if not held.valid_on(upgrade_date):
            continue  # 已到期证书走正常续期，不计入升级补证
        missing = tuple(unit for unit in cert.covers_units if not unit_carryover.get(unit))
        if missing and (held.holder_id, held.cert_id) not in seen:
            seen.add((held.holder_id, held.cert_id))
            supplementary.append(
                SupplementaryRequirement(
                    holder_id=held.holder_id,
                    cert_id=held.cert_id,
                    missing_units=missing,
                    reason="标准升级后部分能力单元不再被新标准覆盖",
                )
            )

    return UpgradePlan(
        old_standard=old_standard,
        new_standard=new_standard,
        upgrade_date=upgrade_date,
        continued_mappings=tuple(continued),
        terminated_mappings=tuple(terminated),
        supplementary=tuple(supplementary),
    )
