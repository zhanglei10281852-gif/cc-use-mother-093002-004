"""识别引擎：给定申请人与任一历史日期，纯函数式地复原当时的认可状态。

不修改任何登记簿，只读取：

* 证书版本目录 / 持证登记簿（当时有效的证书）；
* 证据台账（当时已合规提交、且在查询日仍有效的实操证据）；
* 映射台账（当时处于生效区间的互认映射及其能力单元口径）；
* 岗位目录（当时有效的岗位定义）；
* 案件登记簿（截至当日未决的异议/复议事项）。

依据按作用域归集，避免口径串用：

* 互认映射产生的依据键为 (标准版本, 目标等级口径, 单元)——
  "中级证→中级"的等同不能拿去满足"高级"岗位；
* 实操证据直接证明 (标准版本, 单元)，与等级口径无关，可补充任意等级岗位。

所有判断只使用日期不晚于查询日的事实，因此升级、改判之后的新数据
不会污染历史招聘依据。
"""
from __future__ import annotations

from dataclasses import dataclass

from .certificates import CertificateCatalog, Credential, CredentialRegistry
from .evidence import EvidenceLedger
from .mappings import MappingBook
from .positions import PositionDefinition, PositionRegistry
from .review import CaseRegistry

ASSESSMENT_PASS_PURPOSE = "附加考核通过"
MappingUnitRef = tuple[tuple[str, int], str, str]
EvidenceUnitRef = tuple[tuple[str, int], str]


@dataclass(frozen=True)
class UnitBasis:
    """某能力单元被认可所依据的来源。"""

    mapping_id: str = ""
    evidence_record_id: str = ""
    standard_key: tuple[str, int] | None = None

    def describe(self) -> str:
        if self.mapping_id:
            return f"互认映射 {self.mapping_id}"
        return f"实操证据 {self.evidence_record_id}"


@dataclass(frozen=True)
class PositionAssessment:
    position: PositionDefinition
    eligible: bool
    missing_units: frozenset[str]
    unit_basis: dict[str, list[UnitBasis]]
    standard_version: tuple[str, int]


@dataclass(frozen=True)
class PendingMatter:
    case_id: str
    kind: str  # 异议 / 复议
    since: str
    detail: str
    target_standard_key: tuple[str, int]


@dataclass(frozen=True)
class RecognitionView:
    applicant_id: str
    day: str
    positions: tuple[PositionAssessment, ...]
    pending_matters: tuple[PendingMatter, ...]
    standard_versions_in_force: tuple[tuple[str, int], ...]
    mapping_basis: tuple[str, ...]

    @property
    def recognizable_positions(self) -> tuple[PositionAssessment, ...]:
        return tuple(p for p in self.positions if p.eligible)

    def missing_units_for(self, position_id: str) -> frozenset[str]:
        for p in self.positions:
            if p.position.position_id == position_id:
                return p.missing_units
        raise KeyError(position_id)


def _iter_valid_uses(applicant_id: str, day: str, ledger: EvidenceLedger):
    """申请人在 day 当日或之前合规提交、且证据在查询日仍有效的证据使用。

    台账只保存提交成功（提交时未过期、次数未满）的使用；这里再校验
    查询日有效性，是因为证据可能在提交后、查询前到期。
    """
    for use in ledger.all_uses():
        if use.day > day:
            continue
        record = ledger.get(use.record_id)
        if record.holder_id != applicant_id:
            continue
        if not record.is_valid_on(day):
            continue
        yield use, record


def _collect_recognition(
    applicant_id: str,
    day: str,
    cert_catalog: CertificateCatalog,
    credentials: CredentialRegistry,
    ledger: EvidenceLedger,
    mappings: MappingBook,
) -> tuple[
    dict[MappingUnitRef, list[UnitBasis]],
    dict[EvidenceUnitRef, list[UnitBasis]],
    list[str],
]:
    mapping_basis: dict[MappingUnitRef, list[UnitBasis]] = {}
    evidence_basis: dict[EvidenceUnitRef, list[UnitBasis]] = {}
    used_mapping_ids: list[str] = []

    # 附加考核通过证据：{标准版本: {单元}}
    passed: dict[tuple[str, int], set[str]] = {}
    # 直接证明单元的补充证据（按证据去重）
    supplementary: dict[str, tuple[tuple[str, int], frozenset[str]]] = {}
    for use, record in _iter_valid_uses(applicant_id, day, ledger):
        if not record.units or record.standard_key is None:
            continue
        if use.purpose == ASSESSMENT_PASS_PURPOSE:
            passed.setdefault(record.standard_key, set()).update(record.units)
        else:
            supplementary.setdefault(
                record.record_id, (record.standard_key, frozenset(record.units))
            )

    valid_credentials: list[Credential] = []
    for cred in credentials.valid_for_on(applicant_id, day):
        cert_version = cert_catalog.get(*cred.cert_key)
        if cert_version.is_active_on(day):
            valid_credentials.append(cred)

    for cred in valid_credentials:
        for m in mappings.for_source_on(cred.cert_key, day):
            used_mapping_ids.append(m.mapping_id)
            target = m.target_standard_key
            grade = m.target_grade_label
            for unit in m.covered_units:
                mapping_basis.setdefault((target, grade, unit), []).append(
                    UnitBasis(mapping_id=m.mapping_id, standard_key=target)
                )
            # 附加考核单元：须有该申请人在 day 之前合规提交的考核通过证据。
            for unit in m.assessment_units & passed.get(target, set()):
                mapping_basis.setdefault((target, grade, unit), []).append(
                    UnitBasis(mapping_id=m.mapping_id, standard_key=target)
                )

    for record_id, (std_key, units) in supplementary.items():
        for unit in units:
            evidence_basis.setdefault((std_key, unit), []).append(
                UnitBasis(evidence_record_id=record_id, standard_key=std_key)
            )

    return mapping_basis, evidence_basis, used_mapping_ids


def _pending_matters(applicant_id: str, day: str, cases: CaseRegistry) -> list[PendingMatter]:
    """按案件事件流复原截至 day 的未决异议/复议。"""
    matters: list[PendingMatter] = []
    for case in cases.all_cases():
        if case.applicant_id != applicant_id:
            continue
        open_objection = None
        open_reconsideration = None
        for ev in case.events:
            if ev.on > day:
                break
            if ev.kind == "提出异议":
                open_objection = ev
            elif ev.kind in ("受理异议，进入复议", "启动复议"):
                open_objection = None
                open_reconsideration = ev
            elif ev.kind == "驳回异议，维持原结论":
                open_objection = None
            elif ev.kind == "复议结束":
                open_reconsideration = None
        if open_objection is not None:
            matters.append(
                PendingMatter(
                    case.case_id, "异议", open_objection.on,
                    open_objection.detail, case.target_standard_key,
                )
            )
        if open_reconsideration is not None:
            matters.append(
                PendingMatter(
                    case.case_id, "复议", open_reconsideration.on,
                    open_reconsideration.detail, case.target_standard_key,
                )
            )
    return matters


def assess_applicant(
    applicant_id: str,
    day: str,
    cert_catalog: CertificateCatalog,
    credentials: CredentialRegistry,
    ledger: EvidenceLedger,
    mappings: MappingBook,
    positions: PositionRegistry,
    cases: CaseRegistry,
) -> RecognitionView:
    mapping_basis, evidence_basis, used_mapping_ids = _collect_recognition(
        applicant_id, day, cert_catalog, credentials, ledger, mappings
    )

    assessments: list[PositionAssessment] = []
    standards_in_force: set[tuple[str, int]] = set()
    for pos in positions.effective_on(day):
        standards_in_force.add(pos.standard_key)
        target = pos.standard_key

        def basis_for(unit: str) -> list[UnitBasis]:
            return mapping_basis.get((target, pos.grade_label, unit), []) + evidence_basis.get(
                (target, unit), []
            )

        per_unit_basis = {u: basis_for(u) for u in pos.required_units}
        missing = frozenset(u for u in pos.required_units if not per_unit_basis[u])
        assessments.append(
            PositionAssessment(
                position=pos,
                eligible=not missing,
                missing_units=missing,
                unit_basis=per_unit_basis,
                standard_version=target,
            )
        )

    return RecognitionView(
        applicant_id=applicant_id,
        day=day,
        positions=tuple(assessments),
        pending_matters=tuple(_pending_matters(applicant_id, day, cases)),
        standard_versions_in_force=tuple(sorted(standards_in_force)),
        mapping_basis=tuple(sorted(set(used_mapping_ids))),
    )
