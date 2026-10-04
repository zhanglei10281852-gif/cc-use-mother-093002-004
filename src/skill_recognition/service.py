"""职业能力互认服务门面：登记、评审、标准升级与时点查询。"""
from __future__ import annotations

from datetime import date

from .catalog import (
    CertificateVersion,
    CompetencyUnit,
    CourseOutcome,
    HeldCertificate,
    PositionProfile,
)
from .contracts import StandardVersion
from .evidence import EvidenceLedger, EvidenceUse, PracticalEvidence
from .mapping import (
    EquivalenceMapping,
    MappingBasis,
    MappingRegistry,
    UpgradePlan,
    plan_upgrade,
)
from .recognition import ApplicantReport, Recognition, RecognitionBook, assess_applicant
from .review import Decision, ReviewBoard, ReviewCase, ReviewError


class RecognitionService:
    """把目录、证据、映射、评审与结论台账组合成一套互认服务。"""

    def __init__(self) -> None:
        self._standards: dict[tuple[str, int], StandardVersion] = {}
        self._units: dict[tuple[tuple[str, int], str], CompetencyUnit] = {}
        self._cert_versions: dict[str, CertificateVersion] = {}
        self._held: list[HeldCertificate] = []
        self._courses: list[CourseOutcome] = []
        self._positions: dict[str, PositionProfile] = {}
        self.evidence = EvidenceLedger()
        self.mappings = MappingRegistry()
        self.board = ReviewBoard()
        self.book = RecognitionBook()

    # ---- 目录登记 ----

    def register_standard(self, standard: StandardVersion, units: tuple[CompetencyUnit, ...] = ()) -> None:
        self._standards[standard.key()] = standard
        for unit in units:
            if unit.standard.key() != standard.key():
                raise ValueError("能力单元与标准版本不一致")
            self._units[(standard.key(), unit.unit_code)] = unit

    def _require_units(self, standard: StandardVersion, unit_codes: tuple[str, ...], what: str) -> None:
        if standard.key() not in self._standards:
            raise ValueError(f"{what}所依据的标准版本未登记")
        unknown = [u for u in unit_codes if (standard.key(), u) not in self._units]
        if unknown:
            raise ValueError(f"{what}引用了未登记的能力单元: {', '.join(unknown)}")

    def register_certificate_version(self, cert: CertificateVersion) -> None:
        self._require_units(cert.standard, cert.covers_units, "证书版本")
        self._cert_versions[cert.cert_id] = cert

    def issue_certificate(self, held: HeldCertificate) -> None:
        if held.cert_id not in self._cert_versions:
            raise ValueError("证书版本未登记")
        self._held.append(held)

    def register_course(self, outcome: CourseOutcome) -> None:
        self._courses.append(outcome)

    def register_position(self, position: PositionProfile) -> None:
        self._require_units(position.standard, position.required_units, "岗位画像")
        self._positions[position.position_id] = position

    def register_mapping(self, mapping: EquivalenceMapping) -> None:
        self._require_units(mapping.standard, mapping.target_units, "映射")
        self.mappings.register(mapping)

    def register_evidence(self, evidence: PracticalEvidence) -> None:
        self.evidence.register(evidence)

    # ---- 评审流程 ----

    def open_review(self, case_id: str, applicant_id: str, mapping_id: str, on: date) -> ReviewCase:
        mapping = self.mappings.get(mapping_id)
        if mapping.effective_from > on or (mapping.effective_to is not None and on > mapping.effective_to):
            raise ReviewError("映射在该日期尚未生效或已失效")
        return self.board.open_case(case_id, applicant_id, mapping_id, on)

    def submit_evidence(self, evidence_id: str, case_id: str, on: date) -> EvidenceUse:
        self.board.get(case_id)  # 案件必须存在
        return self.evidence.submit(evidence_id, case_id, on)

    def decide(
        self,
        case_id: str,
        decision: Decision,
        on: date,
        expected_version: int,
        recognized_units: tuple[str, ...] | None = None,
        conditions: tuple[str, ...] = (),
        rationale: str = "",
    ) -> Recognition | None:
        """落案并登记结论；等同/部分等同会生成不可改写的结论快照。

        结论有效期不超过映射有效期，证书类映射还不超过证书到期日——
        旧证书到期后结论自然失效，持证人需按统一规则补证续期。
        """
        case = self.board.get(case_id)
        mapping = self.mappings.get(case.subject_mapping_id)

        recognition = None
        if decision in (Decision.EQUIVALENT, Decision.PARTIAL):
            units = tuple(recognized_units) if recognized_units is not None else mapping.target_units
            if not set(units) <= set(mapping.target_units):
                raise ReviewError("认可的能力单元超出映射范围")
            if decision is Decision.EQUIVALENT and set(units) != set(mapping.target_units):
                raise ReviewError("等同决定须覆盖映射的全部能力单元")
            if decision is Decision.PARTIAL and set(units) == set(mapping.target_units):
                raise ReviewError("部分等同决定不应覆盖映射的全部能力单元")
            valid_to = mapping.effective_to
            if mapping.basis is MappingBasis.CERTIFICATE:
                held = self._held_valid_for(case.applicant_id, mapping.source_id, on)
                if held is None:
                    raise ReviewError("申请人未持有该证书版本的有效证书")
                if held.expires_on is not None:
                    valid_to = min(valid_to, held.expires_on) if valid_to is not None else held.expires_on
            supersedes = None
            if case.reconsideration_of is not None:
                original = self.book.find_by_case(case.reconsideration_of)
                if original is not None:
                    supersedes = original.recognition_id
            recognition = Recognition(
                recognition_id=f"REC-{case_id}",
                applicant_id=case.applicant_id,
                case_id=case_id,
                mapping_id=mapping.mapping_id,
                decision=decision,
                units=units,
                standard=mapping.standard,
                decided_on=on,
                valid_from=on,
                valid_to=valid_to,
                supersedes=supersedes,
                conditions=tuple(conditions),
            )

        # 先落案（含并行与终局检查），再登记结论，保证并行评审不会覆盖已生效结论
        case.apply_decision(decision, on, expected_version, rationale)
        if recognition is not None:
            self.book.record(recognition)
        return recognition

    def file_objection(
        self,
        objection_id: str,
        case_id: str,
        raised_by: str,
        reason: str,
        raised_on: date,
        reconsideration_case_id: str,
    ) -> ReviewCase:
        return self.board.file_objection(objection_id, case_id, raised_by, reason, raised_on, reconsideration_case_id)

    def _held_valid_for(self, applicant_id: str, cert_id: str, on: date) -> HeldCertificate | None:
        for held in self._held:
            if held.holder_id == applicant_id and held.cert_id == cert_id and held.valid_on(on):
                return held
        return None

    # ---- 标准升级 ----

    def plan_upgrade(
        self,
        old_standard: StandardVersion,
        new_standard: StandardVersion,
        unit_carryover: dict[str, str | None],
        upgrade_date: date,
    ) -> UpgradePlan:
        return plan_upgrade(
            old_standard,
            new_standard,
            unit_carryover,
            self.mappings.effective_on(upgrade_date),
            tuple(self._held),
            tuple(self._cert_versions.values()),
            upgrade_date,
        )

    def apply_upgrade(self, plan: UpgradePlan, unit_carryover: dict[str, str | None]) -> None:
        """执行升级：延续的映射平移到新标准，终止的映射退休，相关结论平移或失效。

        所有变化都以新记录追加，历史招聘依据不被改写。
        """
        new_standard = plan.new_standard
        for mapping_id in plan.continued_mappings:
            old = self.mappings.get(mapping_id)
            continued = EquivalenceMapping(
                mapping_id=f"{old.mapping_id}-r{new_standard.revision}",
                basis=old.basis,
                source_id=old.source_id,
                standard=new_standard,
                target_units=tuple(unit_carryover[u] for u in old.target_units),
                effective_from=plan.upgrade_date,
                effective_to=old.effective_to,
                supersedes=old.mapping_id,
            )
            self.mappings.register(continued)
            for record in self.book.latest_records(plan.upgrade_date):
                if record.mapping_id != old.mapping_id:
                    continue
                self.book.record(
                    Recognition(
                        recognition_id=f"{record.recognition_id}-r{new_standard.revision}",
                        applicant_id=record.applicant_id,
                        case_id=record.case_id,
                        mapping_id=continued.mapping_id,
                        decision=record.decision,
                        units=tuple(unit_carryover[u] for u in record.units),
                        standard=new_standard,
                        decided_on=plan.upgrade_date,
                        valid_from=plan.upgrade_date,
                        valid_to=record.valid_to,
                        supersedes=record.recognition_id,
                        conditions=record.conditions,
                    )
                )
        for mapping_id in plan.terminated_mappings:
            self.mappings.retire(mapping_id, plan.upgrade_date)
            for record in self.book.latest_records(plan.upgrade_date):
                if record.mapping_id == mapping_id:
                    self.book.lapse(record.recognition_id, plan.upgrade_date, "标准升级，映射终止")

    # ---- 人事时点查询 ----

    def assess(self, applicant_id: str, as_of: date) -> ApplicantReport:
        """输入申请人与任一历史日期，返回当时可认可的岗位、缺失能力、待复议事项及所依据的标准版本。"""
        return assess_applicant(applicant_id, as_of, tuple(self._positions.values()), self.book, self.board)
