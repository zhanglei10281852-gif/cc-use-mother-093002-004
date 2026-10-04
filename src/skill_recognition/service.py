"""职业能力互认服务门面。

把各登记簿与识别引擎组装成一组用例：

* :meth:`submit_evidence` 提交实操证据（核销有效期/次数，同一令牌幂等）；
* :meth:`record_conclusion` 把评审结论落为互认映射，复议改判时旧映射
  在新结论生效日被替代但完整保留；
* :meth:`apply_standard_upgrade` 发布标准升级并计算映射延续性与受影响持证人；
* :meth:`carry_forward` / :meth:`send_mapping_to_review` 处置升级分析结果；
* :meth:`recognize` 供人力部门按申请人 + 任一历史日期复原认可状态。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .certificates import CertificateCatalog, CredentialRegistry
from .certificates import RenewalRule
from .engine import RecognitionView, assess_applicant
from .errors import EffectiveConclusionConflict, ValidationError
from .evidence import EvidenceLedger
from .mappings import (
    MappingBook,
    MappingDecision,
    MappingStatus,
    RecognitionMapping,
    validate_mapping_against_versions,
)
from .positions import PositionRegistry
from .review import (
    CaseConclusion,
    CaseRegistry,
    CaseStatus,
    ReconsiderationOutcome,
)
from .standards import StandardRegistry


CONTINUES = "继续有效"
NEEDS_REVIEW = "需复议"


@dataclass(frozen=True)
class RenewalInstruction:
    """旧证书到期后按其版本登记的补证规则给出的一致处置指引。"""

    credential_id: str
    cert_key: tuple[str, int]
    rule: str
    expired: bool
    instruction: str


@dataclass(frozen=True)
class MappingUpgradeVerdict:
    mapping: RecognitionMapping
    verdict: str
    lost_units: frozenset[str] = field(default_factory=frozenset)
    added_required_units: frozenset[str] = field(default_factory=frozenset)
    reason: str = ""


@dataclass(frozen=True)
class AffectedHolder:
    holder_id: str
    credential_id: str
    cert_key: tuple[str, int]
    expired: bool
    missing_units: frozenset[str]


@dataclass(frozen=True)
class UpgradeImpact:
    old_standard_key: tuple[str, int]
    new_standard_key: tuple[str, int]
    retire_date: str
    verdicts: tuple[MappingUpgradeVerdict, ...]
    affected_holders: tuple[AffectedHolder, ...]
    positions_anchored_to_old: tuple[str, ...]

    @property
    def continuing_mappings(self) -> tuple[MappingUpgradeVerdict, ...]:
        return tuple(v for v in self.verdicts if v.verdict == CONTINUES)

    @property
    def review_mappings(self) -> tuple[MappingUpgradeVerdict, ...]:
        return tuple(v for v in self.verdicts if v.verdict == NEEDS_REVIEW)


class RecognitionService:
    def __init__(self, objection_window_days: int = 30) -> None:
        self.standards = StandardRegistry()
        self.certificates = CertificateCatalog()
        self.credentials = CredentialRegistry()
        self.ledger = EvidenceLedger()
        self.mappings = MappingBook()
        self.positions = PositionRegistry()
        self.cases = CaseRegistry(objection_window_days=objection_window_days)

    # ---- 证据使用 ----

    _RENEWAL_INSTRUCTIONS = {
        RenewalRule.NOT_RENEWABLE: "该证书版本不予补证，须持有效新证书重新申请互认",
        RenewalRule.SUPPLEMENT_EVIDENCE: "补交有效期内的实操证据后，可恢复对应能力认可",
        RenewalRule.FULL_RE_REVIEW: "须按现行标准重新进行互认评审",
    }

    def renewal_instruction(self, credential_id: str, day: str) -> RenewalInstruction:
        """按证书版本登记的补证规则，给出到期后的一致处置指引。"""
        cred = self.credentials.get(credential_id)
        cert_version = self.certificates.get(*cred.cert_key)
        return RenewalInstruction(
            credential_id=credential_id,
            cert_key=cred.cert_key,
            rule=cert_version.renewal_rule.value,
            expired=not cred.is_valid_on(day),
            instruction=self._RENEWAL_INSTRUCTIONS[cert_version.renewal_rule],
        )

    def submit_evidence(
        self,
        record_id: str,
        case_id: str,
        day: str,
        token: str,
        purpose: str = "互认评审",
    ):
        """向案件提交证据。次数核销与有效期校验由台账完成，跨案件重放不放行。"""
        return self.ledger.submit_use(record_id, case_id, day, purpose, token)

    # ---- 评审结论落映射 ----

    def record_conclusion(
        self,
        case_id: str,
        conclusion: CaseConclusion,
        expected_version: int,
        mapping_id: str,
        basis: str = "",
        supersedes_mapping_id: str = "",
    ) -> RecognitionMapping:
        """登记案件结论为互认映射。

        supersedes_mapping_id 用于跨案件的规则修订：必须显式指向同口径的
        旧映射，旧映射在新结论生效日被替代；不允许静默覆盖其他案件结论。
        """
        case = self.cases.get(case_id)
        cert_version = self.certificates.get(*case.source_cert_key)
        target = self.standards.get(*case.target_standard_key)

        prior_effective = [
            m
            for m in self.mappings.all()
            if m.source_case_id == case_id and m.status is MappingStatus.EFFECTIVE
        ]
        external_replacements: list[RecognitionMapping] = []
        if supersedes_mapping_id:
            old_rule = self.mappings.get(supersedes_mapping_id)
            if (
                old_rule.source_cert_key != case.source_cert_key
                or old_rule.target_standard_key != case.target_standard_key
                or old_rule.target_grade_label != case.target_grade_label
                or not old_rule.is_current()
            ):
                raise ValidationError(
                    f"被替代映射 {supersedes_mapping_id} 与本案口径不一致或已失效"
                )
            if old_rule.source_case_id == case_id:
                raise ValidationError("本案件旧结论会被自动替代，无需显式声明")
            external_replacements.append(old_rule)

        mapping = RecognitionMapping(
            mapping_id=mapping_id,
            source_cert_key=case.source_cert_key,
            target_standard_key=case.target_standard_key,
            target_grade_label=case.target_grade_label,
            decision=conclusion.decision,
            covered_units=conclusion.covered_units,
            assessment_units=conclusion.assessment_units,
            valid_from=conclusion.effective_on,
            source_case_id=case_id,
            basis=basis or conclusion.rationale,
            supersedes=(
                supersedes_mapping_id
                or (prior_effective[0].mapping_id if prior_effective else "")
            ),
        )
        # 先做全部领域校验，再动案件状态，避免校验失败留下半生效结论。
        if self.mappings.exists(mapping_id):
            raise ValidationError(f"映射编号已存在: {mapping_id}")
        clash = self.mappings.effective_rules(
            case.source_cert_key, case.target_standard_key,
            case.target_grade_label, conclusion.effective_on,
            exclude_case_id=case_id,
        )
        clash = [c for c in clash if c.mapping_id != supersedes_mapping_id]
        if clash:
            raise EffectiveConclusionConflict(
                "同一证书版本对该标准等级口径已有生效映射 "
                f"{clash[0].mapping_id}（案件 {clash[0].source_case_id}），"
                "平行评审不得覆盖；如需变更须先替代旧规则"
            )
        for prior in prior_effective:
            if conclusion.effective_on <= prior.valid_from:
                raise ValidationError(
                    "改判结论生效日期必须晚于原结论生效日期，不能回溯改写历史依据"
                )
        validate_mapping_against_versions(
            mapping, cert_version.covered_units, target.units
        )
        self.cases.issue_conclusion(case_id, conclusion, expected_version)
        self.mappings.add(mapping)
        # 旧映射在新结论生效日起失效，历史区间原样保留：
        # 包括本案件复议改判与显式声明替代的跨案件旧规则。
        # 案件乐观锁已挡住同案件并行结论；同口径唯一检查挡住跨案件静默覆盖。
        for prior in [*prior_effective, *external_replacements]:
            self.mappings.terminate(
                prior.mapping_id,
                valid_to=conclusion.effective_on,
                superseded_by_new=mapping_id,
            )
        return mapping

    def resolve_reconsideration(
        self,
        case_id: str,
        reconsideration_id: str,
        outcome: ReconsiderationOutcome,
        reviewer_id: str,
        expected_version: int,
        on: str,
        *,
        revised: CaseConclusion | None = None,
        new_mapping_id: str = "",
        target_standard_key: tuple[str, int] | None = None,
        target_grade_label: str = "",
        note: str = "",
    ) -> RecognitionMapping | None:
        """结束复议。改判时新映射可指向升级后的新标准版本。

        维持原结论返回 None；改判返回新映射，旧映射（若仍生效）在
        新结论生效日被替代并完整保留。
        """
        case = self.cases.get(case_id)
        if outcome is ReconsiderationOutcome.REVISED:
            if revised is None or not new_mapping_id:
                raise ValueError("复议改判必须提供新结论与新映射编号")
            std_key = target_standard_key or case.target_standard_key
            grade = target_grade_label or case.target_grade_label
            cert_version = self.certificates.get(*case.source_cert_key)
            target = self.standards.get(*std_key)
            prior_effective = [
                m
                for m in self.mappings.all()
                if m.source_case_id == case_id and m.status is MappingStatus.EFFECTIVE
            ]
            mapping = RecognitionMapping(
                mapping_id=new_mapping_id,
                source_cert_key=case.source_cert_key,
                target_standard_key=std_key,
                target_grade_label=grade,
                decision=revised.decision,
                covered_units=revised.covered_units,
                assessment_units=revised.assessment_units,
                valid_from=revised.effective_on,
                source_case_id=case_id,
                basis=f"复议改判（{reconsideration_id}）：{revised.rationale}",
                supersedes=prior_effective[0].mapping_id if prior_effective else "",
            )
            validate_mapping_against_versions(
                mapping, cert_version.covered_units, target.units
            )
            if self.mappings.exists(new_mapping_id):
                raise ValidationError(f"映射编号已存在: {new_mapping_id}")
            clash = self.mappings.effective_rules(
                case.source_cert_key, std_key, grade, revised.effective_on,
                exclude_case_id=case_id,
            )
            if clash:
                raise EffectiveConclusionConflict(
                    "同一证书版本对该标准等级口径已有生效映射 "
                    f"{clash[0].mapping_id}（案件 {clash[0].source_case_id}），"
                    "复议结论不得覆盖其他案件的生效规则"
                )
            for prior in prior_effective:
                if revised.effective_on <= prior.valid_from:
                    raise ValidationError(
                        "改判结论生效日期必须晚于原结论生效日期，不能回溯改写历史依据"
                    )
            self.cases.complete_reconsideration(
                case_id, reconsideration_id, outcome, reviewer_id,
                expected_version, on, revised, note,
            )
            self.mappings.add(mapping)
            for prior in prior_effective:
                self.mappings.terminate(
                    prior.mapping_id, revised.effective_on,
                    superseded_by_new=new_mapping_id,
                )
            return mapping

        self.cases.complete_reconsideration(
            case_id, reconsideration_id, outcome, reviewer_id,
            expected_version, on, None, note,
        )
        return None

    # ---- 标准升级 ----

    def apply_standard_upgrade(
        self, new_version, retire_date: str
    ) -> UpgradeImpact:
        """登记升级版本（旧版本内容不变，只追加废止事实）并计算影响。"""
        old_key = (new_version.entity_id, new_version.predecessor_revision)
        self.standards.publish_successor(new_version, retire_date)
        new_std = self.standards.get(*new_version.key)

        verdicts: list[MappingUpgradeVerdict] = []
        for m in self.mappings.all():
            if m.target_standard_key != old_key:
                continue
            if m.status is not MappingStatus.EFFECTIVE:
                continue  # 历史映射只作追溯，不参与升级延续计算
            verdicts.append(self._verdict_for(m, new_std.units))

        affected: list[AffectedHolder] = []
        for cred in self._all_credentials():
            verdict = next(
                (v for v in verdicts if v.mapping.source_cert_key == cred.cert_key),
                None,
            )
            if verdict is None:
                continue
            expired = not cred.is_valid_on(retire_date)
            # 需要补充证明的单元 = 新标准要求 - 旧映射已覆盖单元。
            # 证据锚定旧标准版本，不自动延伸，同样需要重新提交。
            missing = frozenset(new_std.units - verdict.mapping.covered_units)
            # 需复议者即使暂时没有缺口（如仅删除单元），也要列出跟踪；
            # 有缺口或证书到期者同样列出。
            if missing or expired or verdict.verdict == NEEDS_REVIEW:
                affected.append(
                    AffectedHolder(
                        cred.holder_id, cred.credential_id, cred.cert_key,
                        expired, missing,
                    )
                )
        anchored = tuple(sorted({
            p.position_id
            for p in self.positions.effective_on(retire_date)
            if p.standard_key == old_key
        }))
        return UpgradeImpact(
            old_standard_key=old_key,
            new_standard_key=new_version.key,
            retire_date=retire_date,
            verdicts=tuple(verdicts),
            affected_holders=tuple(affected),
            positions_anchored_to_old=anchored,
        )

    def _all_credentials(self):
        return [self.credentials.get(cid) for cid in self.credentials.all_ids()]

    @staticmethod
    def _verdict_for(mapping: RecognitionMapping, new_units: frozenset[str]):
        declared = mapping.covered_units | mapping.assessment_units
        lost = declared - new_units
        added = new_units - mapping.covered_units
        if mapping.decision is MappingDecision.NOT_RECOGNIZED:
            return MappingUpgradeVerdict(
                mapping, NEEDS_REVIEW, reason="不予互认结论须按新标准重审"
            )
        if lost:
            return MappingUpgradeVerdict(
                mapping, NEEDS_REVIEW, lost, added,
                "映射引用的能力单元在新标准中已移除或调整",
            )
        if mapping.decision is MappingDecision.EQUIVALENT:
            if added:
                return MappingUpgradeVerdict(
                    mapping, NEEDS_REVIEW, lost, added,
                    "新标准增加了能力单元，等同结论不再成立",
                )
            return MappingUpgradeVerdict(mapping, CONTINUES, reason="单元口径未变")
        if mapping.decision is MappingDecision.PARTIAL_EQUIVALENT:
            if not mapping.covered_units < new_units:
                return MappingUpgradeVerdict(
                    mapping, NEEDS_REVIEW, lost, added,
                    "旧缺口消失，部分等同的语义需重新评定",
                )
            return MappingUpgradeVerdict(
                mapping, CONTINUES, reason="已认可单元均保留，缺口按新标准自动呈现"
            )
        # ADDITIONAL_ASSESSMENT
        if mapping.covered_units - new_units or not mapping.assessment_units <= new_units:
            return MappingUpgradeVerdict(
                mapping, NEEDS_REVIEW, lost, added, "附加考核单元口径发生变化"
            )
        return MappingUpgradeVerdict(mapping, CONTINUES, reason="考核单元仍存在")

    def carry_forward(
        self,
        old_mapping_id: str,
        new_mapping_id: str,
        carried_by: str,
        on: str | None = None,
    ) -> RecognitionMapping:
        """把判定为"继续有效"的映射延续到新标准版本。"""
        old = self.mappings.get(old_mapping_id)
        retirement = self.standards.retirement_of(old.target_standard_key)
        if retirement is None:
            raise ValueError("目标标准尚未升级，无法延续")
        new_std = self.standards.get(*retirement.successor_key)
        verdict = self._verdict_for(old, new_std.units)
        if verdict.verdict != CONTINUES:
            raise ValueError(f"映射不能自动延续: {verdict.reason}")
        effective_on = on or retirement.retire_date
        carried = RecognitionMapping(
            mapping_id=new_mapping_id,
            source_cert_key=old.source_cert_key,
            target_standard_key=retirement.successor_key,
            target_grade_label=old.target_grade_label,
            decision=old.decision,
            covered_units=old.covered_units,
            assessment_units=old.assessment_units,
            valid_from=effective_on,
            source_case_id=old.source_case_id,
            basis=f"标准升级自动延续自 {old_mapping_id}（{carried_by}）",
            supersedes=old_mapping_id,
        )
        validate_mapping_against_versions(
            carried,
            self.certificates.get(*old.source_cert_key).covered_units,
            new_std.units,
        )
        self.mappings.add(carried)
        self.mappings.terminate(
            old_mapping_id, retirement.retire_date, superseded_by_new=new_mapping_id
        )
        return carried

    def send_mapping_to_review(
        self,
        old_mapping_id: str,
        reconsideration_id: str,
        reviewer_id: str,
        on: str,
        note: str = "",
    ):
        """把受升级影响的映射来源案件送入复议；旧映射在标准废止日失效。"""
        old = self.mappings.get(old_mapping_id)
        retirement = self.standards.retirement_of(old.target_standard_key)
        if retirement is None:
            raise ValueError("目标标准尚未升级")
        case = self.cases.get(old.source_case_id)
        if case.status in (CaseStatus.DECIDED, CaseStatus.CLOSED):
            self.cases.start_reconsideration(
                case.case_id, reconsideration_id, reviewer_id,
                case.version, on, "标准升级", note,
            )
        elif case.status in (CaseStatus.OBJECTED, CaseStatus.RECONSIDERING):
            pass  # 异议/复议已在途，不重复立案
        self.mappings.terminate(old_mapping_id, retirement.retire_date)

    # ---- 人力查询 ----

    def recognize(self, applicant_id: str, day: str) -> RecognitionView:
        """返回申请人在任一历史日期可认可的岗位、缺失能力与待复议事项。"""
        return assess_applicant(
            applicant_id,
            day,
            self.certificates,
            self.credentials,
            self.ledger,
            self.mappings,
            self.positions,
            self.cases,
        )
