"""评审案件：评审组、利益冲突回避、异议、复议与并发保护。

案件状态机：

    OPEN ──作出生效结论──▶ DECIDED ──窗口期内提出异议──▶ OBJECTED
      ▲                       │                            │
      │                       │驳回异议（回到 DECIDED）     │受理
      │                       ▼                            ▼
      └────────────── RECONSIDERING ──维持原判──▶ CLOSED
                              │
                              └──复议改判──▶ DECIDED（新结论另行生效，
                                              旧映射按替代流程失效）

所有修改案件的操作都要带 expected_version（乐观锁）：并行评审中后提交的
一方会收到 ConcurrentReviewError，不能把已经生效的结论静默覆盖。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .errors import (
    CaseStateError,
    ConcurrentReviewError,
    ReviewerConflictError,
    ValidationError,
)
from .mappings import MappingDecision


class CaseStatus(str, Enum):
    OPEN = "评审中"
    DECIDED = "结论已生效"
    OBJECTED = "异议处理中"
    RECONSIDERING = "复议中"
    CLOSED = "已终结"


class ObjectionStatus(str, Enum):
    PENDING = "待受理"
    ACCEPTED = "已受理"
    REJECTED = "已驳回"


class ReconsiderationOutcome(str, Enum):
    ORIGINAL_UPHELD = "维持原结论"
    REVISED = "复议改判"


@dataclass(frozen=True)
class Reviewer:
    reviewer_id: str
    name: str
    affiliations: frozenset[str] = field(default_factory=frozenset)
    active: bool = True

    def __post_init__(self) -> None:
        if not self.reviewer_id or not self.name:
            raise ValidationError("评审人信息不完整")


@dataclass(frozen=True)
class ConflictDeclaration:
    """利益冲突声明与回避记录。"""

    reviewer_id: str
    related_party: str
    reason: str
    declared_on: str
    recused: bool = True


@dataclass(frozen=True)
class CaseConclusion:
    decision: MappingDecision
    decided_on: str
    effective_on: str
    covered_units: frozenset[str] = field(default_factory=frozenset)
    assessment_units: frozenset[str] = field(default_factory=frozenset)
    rationale: str = ""
    issued_by: str = ""

    def __post_init__(self) -> None:
        if self.effective_on < self.decided_on:
            raise ValidationError("结论生效日期不能早于作出日期")


@dataclass(frozen=True)
class Objection:
    objection_id: str
    raised_by: str
    raised_on: str
    grounds: str

    def __post_init__(self) -> None:
        if not self.objection_id or not self.raised_by or not self.grounds:
            raise ValidationError("异议信息不完整")


@dataclass(frozen=True)
class _RuledObjection:
    objection: Objection
    status: ObjectionStatus
    ruled_on: str | None
    note: str


@dataclass(frozen=True)
class ReconsiderationRecord:
    reconsideration_id: str
    requested_on: str
    trigger: str  # 异议受理 / 标准升级 / 评审组自查
    completed_on: str | None
    outcome: ReconsiderationOutcome | None
    note: str


@dataclass(frozen=True)
class CaseEvent:
    seq: int
    on: str
    actor: str
    kind: str
    detail: str


@dataclass
class ReviewCase:
    case_id: str
    applicant_id: str
    source_cert_key: tuple[str, int]
    target_standard_key: tuple[str, int]
    target_grade_label: str
    involved_parties: frozenset[str]
    created_on: str
    status: CaseStatus = CaseStatus.OPEN
    version: int = 1
    panel: list[str] = field(default_factory=list)
    recused: dict[str, ConflictDeclaration] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    conclusion: CaseConclusion | None = None
    past_conclusions: list[CaseConclusion] = field(default_factory=list)
    objections: list[_RuledObjection] = field(default_factory=list)
    reconsiderations: list[ReconsiderationRecord] = field(default_factory=list)
    events: list[CaseEvent] = field(default_factory=list)

    def active_panel(self) -> list[str]:
        return [r for r in self.panel if r not in self.recused]

    def _event(self, on: str, actor: str, kind: str, detail: str) -> None:
        self.events.append(CaseEvent(len(self.events) + 1, on, actor, kind, detail))


class CaseRegistry:
    """评审案件登记簿，同时管理评审人与回避记录。"""

    def __init__(self, objection_window_days: int = 30) -> None:
        self.objection_window_days = objection_window_days
        self._reviewers: dict[str, Reviewer] = {}
        self._cases: dict[str, ReviewCase] = {}

    # ---- 评审人 ----

    def register_reviewer(self, reviewer: Reviewer) -> Reviewer:
        if reviewer.reviewer_id in self._reviewers:
            raise ValidationError(f"评审人已登记: {reviewer.reviewer_id}")
        self._reviewers[reviewer.reviewer_id] = reviewer
        return reviewer

    def reviewer(self, reviewer_id: str) -> Reviewer:
        try:
            return self._reviewers[reviewer_id]
        except KeyError:
            raise ValidationError(f"评审人不存在: {reviewer_id}") from None

    # ---- 案件 ----

    def open_case(
        self,
        case_id: str,
        applicant_id: str,
        source_cert_key: tuple[str, int],
        target_standard_key: tuple[str, int],
        target_grade_label: str,
        involved_parties: frozenset[str] | set[str],
        created_on: str,
    ) -> ReviewCase:
        if case_id in self._cases:
            raise ValidationError(f"案件已存在: {case_id}")
        case = ReviewCase(
            case_id=case_id,
            applicant_id=applicant_id,
            source_cert_key=source_cert_key,
            target_standard_key=target_standard_key,
            target_grade_label=target_grade_label,
            involved_parties=frozenset(involved_parties),
            created_on=created_on,
        )
        case._event(created_on, "系统", "立案", f"申请人 {applicant_id}")
        self._cases[case_id] = case
        return case

    def get(self, case_id: str) -> ReviewCase:
        try:
            return self._cases[case_id]
        except KeyError:
            raise ValidationError(f"案件不存在: {case_id}") from None

    def all_cases(self) -> list[ReviewCase]:
        return list(self._cases.values())

    def _load(
        self, case_id: str, expected_version: int, *allowed: CaseStatus
    ) -> ReviewCase:
        case = self.get(case_id)
        if case.version != expected_version:
            raise ConcurrentReviewError(
                f"案件 {case_id} 版本过期：持有 {expected_version}，当前 {case.version}"
            )
        if allowed and case.status not in allowed:
            raise CaseStateError(
                f"案件 {case_id} 当前状态 {case.status.value}，不允许该操作"
            )
        return case

    @staticmethod
    def _commit(case: ReviewCase) -> None:
        case.version += 1

    def _check_no_conflict(self, case: ReviewCase, reviewer: Reviewer) -> None:
        conflict = set(reviewer.affiliations) & set(case.involved_parties)
        if conflict or reviewer.reviewer_id in case.recused:
            raise ReviewerConflictError(
                f"评审人 {reviewer.name} 与案件 {case.case_id} 存在利益冲突，"
                f"应回避（关联方: {sorted(conflict) or '已声明回避'}）"
            )
        if not reviewer.active:
            raise ReviewerConflictError(f"评审人 {reviewer.name} 已被停用")

    def assign_reviewer(
        self, case_id: str, reviewer_id: str, expected_version: int, on: str
    ) -> ReviewCase:
        case = self._load(case_id, expected_version, CaseStatus.OPEN)
        reviewer = self.reviewer(reviewer_id)
        self._check_no_conflict(case, reviewer)
        if reviewer_id in case.panel:
            raise ValidationError("评审人已在评审组中")
        case.panel.append(reviewer_id)
        case._event(on, reviewer_id, "加入评审组", "")
        self._commit(case)
        return case

    def declare_conflict(
        self,
        case_id: str,
        reviewer_id: str,
        related_party: str,
        reason: str,
        expected_version: int,
        on: str,
    ) -> ReviewCase:
        """评审人主动声明利益冲突并回避（即使登记簿未预先登记关联）。"""
        case = self._load(case_id, expected_version, CaseStatus.OPEN, CaseStatus.DECIDED)
        reviewer = self.reviewer(reviewer_id)
        declaration = ConflictDeclaration(reviewer_id, related_party, reason, on, True)
        case.recused[reviewer_id] = declaration
        case._event(on, reviewer_id, "回避", f"{related_party}: {reason}")
        self._commit(case)
        return case

    def attach_evidence(
        self, case_id: str, evidence_record_id: str, expected_version: int, on: str
    ) -> ReviewCase:
        case = self._load(case_id, expected_version, CaseStatus.OPEN)
        if evidence_record_id in case.evidence_ids:
            raise ValidationError("证据已附在本案件，请勿重复提交")
        case.evidence_ids.append(evidence_record_id)
        case._event(on, "系统", "附入证据", evidence_record_id)
        self._commit(case)
        return case

    def issue_conclusion(
        self,
        case_id: str,
        conclusion: CaseConclusion,
        expected_version: int,
    ) -> ReviewCase:
        case = self._load(case_id, expected_version, CaseStatus.OPEN)
        reviewer = self.reviewer(conclusion.issued_by)
        self._check_no_conflict(case, reviewer)
        if conclusion.issued_by not in case.active_panel():
            raise CaseStateError("只有未回避的评审组成员可以作出结论")
        if not case.active_panel():
            raise CaseStateError("评审组无有效成员，不能作出结论")
        case.conclusion = conclusion
        case.status = CaseStatus.DECIDED
        case._event(
            conclusion.decided_on,
            conclusion.issued_by,
            "作出生效结论",
            f"{conclusion.decision.value} / {conclusion.rationale}",
        )
        self._commit(case)
        return case

    def file_objection(
        self,
        case_id: str,
        objection: Objection,
        expected_version: int,
    ) -> ReviewCase:
        case = self._load(case_id, expected_version, CaseStatus.DECIDED)
        assert case.conclusion is not None
        # 异议窗口按结论生效日起算
        delta = _day_diff(case.conclusion.decided_on, objection.raised_on)
        if delta < 0:
            raise CaseStateError("异议日期不能早于结论日期")
        if delta > self.objection_window_days:
            raise CaseStateError(
                f"已超过 {self.objection_window_days} 日异议期，不能再提出异议"
            )
        if any(o.objection.objection_id == objection.objection_id for o in case.objections):
            raise ValidationError("异议重复提交")
        case.objections.append(_RuledObjection(objection, ObjectionStatus.PENDING, None, ""))
        case.status = CaseStatus.OBJECTED
        case._event(objection.raised_on, objection.raised_by, "提出异议", objection.grounds)
        self._commit(case)
        return case

    def rule_objection(
        self,
        case_id: str,
        objection_id: str,
        accept: bool,
        reviewer_id: str,
        expected_version: int,
        on: str,
        note: str = "",
        reconsideration_id: str = "",
    ) -> ReviewCase:
        case = self._load(case_id, expected_version, CaseStatus.OBJECTED)
        reviewer = self.reviewer(reviewer_id)
        self._check_no_conflict(case, reviewer)
        for i, ruled in enumerate(case.objections):
            if ruled.objection.objection_id == objection_id:
                if ruled.status is not ObjectionStatus.PENDING:
                    raise CaseStateError("该异议已处理")
                if accept:
                    if not reconsideration_id:
                        raise ValidationError("受理异议必须登记复议编号")
                    case.objections[i] = _RuledObjection(
                        ruled.objection, ObjectionStatus.ACCEPTED, on, note
                    )
                    case.reconsiderations.append(
                        ReconsiderationRecord(
                            reconsideration_id, on, "异议受理", None, None, note
                        )
                    )
                    case.status = CaseStatus.RECONSIDERING
                    kind = "受理异议，进入复议"
                else:
                    case.objections[i] = _RuledObjection(
                        ruled.objection, ObjectionStatus.REJECTED, on, note
                    )
                    case.status = CaseStatus.DECIDED
                    kind = "驳回异议，维持原结论"
                case._event(on, reviewer_id, kind, f"{objection_id} {note}")
                self._commit(case)
                return case
        raise ValidationError(f"异议不存在: {objection_id}")

    def start_reconsideration(
        self,
        case_id: str,
        reconsideration_id: str,
        reviewer_id: str,
        expected_version: int,
        on: str,
        trigger: str,
        note: str = "",
    ) -> ReviewCase:
        """不经过异议直接启动复议（如标准升级后的统一复议）。"""
        case = self._load(case_id, expected_version, CaseStatus.DECIDED, CaseStatus.CLOSED)
        reviewer = self.reviewer(reviewer_id)
        self._check_no_conflict(case, reviewer)
        if any(r.reconsideration_id == reconsideration_id for r in case.reconsiderations):
            raise ValidationError("复议编号重复")
        case.reconsiderations.append(
            ReconsiderationRecord(reconsideration_id, on, trigger, None, None, note)
        )
        case.status = CaseStatus.RECONSIDERING
        case._event(on, reviewer_id, "启动复议", f"{trigger} {note}")
        self._commit(case)
        return case

    def complete_reconsideration(
        self,
        case_id: str,
        reconsideration_id: str,
        outcome: ReconsiderationOutcome,
        reviewer_id: str,
        expected_version: int,
        on: str,
        revised: CaseConclusion | None = None,
        note: str = "",
    ) -> ReviewCase:
        case = self._load(case_id, expected_version, CaseStatus.RECONSIDERING)
        reviewer = self.reviewer(reviewer_id)
        self._check_no_conflict(case, reviewer)
        if outcome is ReconsiderationOutcome.REVISED:
            if revised is None:
                raise ValidationError("复议改判必须提供新结论")
            self._check_no_conflict(case, self.reviewer(revised.issued_by))
            if revised.issued_by not in case.active_panel():
                raise CaseStateError("改判结论须由未回避的评审组成员作出")
        for i, rec in enumerate(case.reconsiderations):
            if rec.reconsideration_id == reconsideration_id:
                if rec.completed_on is not None:
                    raise CaseStateError("该复议已结束")
                case.reconsiderations[i] = ReconsiderationRecord(
                    rec.reconsideration_id, rec.requested_on, rec.trigger, on, outcome, note
                )
                break
        else:
            raise ValidationError(f"复议不存在: {reconsideration_id}")
        if outcome is ReconsiderationOutcome.REVISED and revised is not None:
            assert case.conclusion is not None
            case.past_conclusions.append(case.conclusion)
            case.conclusion = revised
            case.status = CaseStatus.DECIDED
            detail = "复议改判，新结论另行生效"
        else:
            case.status = CaseStatus.CLOSED
            detail = "复议维持原结论"
        case._event(on, reviewer_id, "复议结束", f"{detail} {note}")
        self._commit(case)
        return case


def _day_diff(a: str, b: str) -> int:
    """ISO 日期字符串之间的天数差（b - a），用公历日期表计算，避免引入依赖。"""
    from datetime import date

    da = date.fromisoformat(a)
    db = date.fromisoformat(b)
    return (db - da).days
