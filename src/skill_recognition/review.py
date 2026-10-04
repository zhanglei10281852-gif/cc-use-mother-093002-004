"""评审：案件、投票、回避、决定、异议与复议。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class ReviewError(Exception):
    """评审流程错误基类。"""


class ConflictOfInterestError(ReviewError):
    """利益冲突：已回避的评审人不得参与。"""


class ConcurrencyError(ReviewError):
    """并行冲突：案件版本已变化，不能覆盖他人结论。"""


class DecisionFinalError(ReviewError):
    """已生效结论不可覆盖，须通过复议程序。"""


class Decision(str, Enum):
    """评审组可作出的四类决定。"""

    EQUIVALENT = "等同"
    PARTIAL = "部分等同"
    ADDITIONAL_ASSESSMENT = "附加考核"
    REJECTED = "不予互认"


class CaseStatus(str, Enum):
    OPEN = "评审中"
    DECIDED = "已生效"


@dataclass(frozen=True)
class Vote:
    reviewer: str
    decision: Decision
    rationale: str
    cast_on: date


@dataclass(frozen=True)
class Recusal:
    """利益冲突回避记录。"""

    reviewer: str
    reason: str
    declared_on: date


@dataclass(frozen=True)
class Objection:
    """对已生效结论提出的异议。"""

    objection_id: str
    case_id: str
    raised_by: str
    reason: str
    raised_on: date


class ReviewCase:
    """评审案件：对某申请人按某条映射的互认请求进行评审。"""

    def __init__(
        self,
        case_id: str,
        applicant_id: str,
        subject_mapping_id: str,
        opened_on: date,
        reconsideration_of: str | None = None,
    ) -> None:
        if not case_id or not applicant_id or not subject_mapping_id:
            raise ValueError("案件信息不完整")
        self.case_id = case_id
        self.applicant_id = applicant_id
        self.subject_mapping_id = subject_mapping_id
        self.opened_on = opened_on
        self.reconsideration_of = reconsideration_of
        self.status = CaseStatus.OPEN
        self.version = 0  # 乐观锁：任何变更都递增
        self.votes: list[Vote] = []
        self.recusals: list[Recusal] = []
        self.decision: Decision | None = None
        self.decided_on: date | None = None
        self.decision_rationale = ""

    def declare_recusal(self, reviewer: str, reason: str, on: date) -> None:
        if any(r.reviewer == reviewer for r in self.recusals):
            return
        self.recusals.append(Recusal(reviewer, reason, on))
        self.version += 1

    def cast_vote(self, reviewer: str, decision: Decision, rationale: str, on: date) -> None:
        if self.status is CaseStatus.DECIDED:
            raise DecisionFinalError("案件已结案，不能再投票")
        if any(r.reviewer == reviewer for r in self.recusals):
            raise ConflictOfInterestError(f"评审人 {reviewer} 已声明回避，不得参与本案")
        if any(v.reviewer == reviewer for v in self.votes):
            raise ReviewError(f"评审人 {reviewer} 已投过票")
        self.votes.append(Vote(reviewer, decision, rationale, on))
        self.version += 1

    def apply_decision(self, decision: Decision, on: date, expected_version: int, rationale: str = "") -> None:
        """落案生效：版本不匹配或已有生效结论时拒绝，保证并行评审不互相覆盖。"""
        if expected_version != self.version:
            raise ConcurrencyError("案件版本已变化，并行评审不能覆盖他人结论")
        if self.status is CaseStatus.DECIDED:
            raise DecisionFinalError("已生效结论不可覆盖，请通过复议程序")
        self.decision = decision
        self.decided_on = on
        self.decision_rationale = rationale
        self.status = CaseStatus.DECIDED
        self.version += 1


class ReviewBoard:
    """评审委员会台账：案件、异议与复议的登记和查询。"""

    def __init__(self) -> None:
        self._cases: dict[str, ReviewCase] = {}
        self._objections: list[Objection] = []

    def open_case(
        self,
        case_id: str,
        applicant_id: str,
        subject_mapping_id: str,
        opened_on: date,
        reconsideration_of: str | None = None,
    ) -> ReviewCase:
        if case_id in self._cases:
            raise ValueError("案件编号已存在")
        case = ReviewCase(case_id, applicant_id, subject_mapping_id, opened_on, reconsideration_of)
        self._cases[case_id] = case
        return case

    def get(self, case_id: str) -> ReviewCase:
        try:
            return self._cases[case_id]
        except KeyError:
            raise KeyError(f"案件不存在: {case_id}") from None

    def file_objection(
        self,
        objection_id: str,
        case_id: str,
        raised_by: str,
        reason: str,
        raised_on: date,
        reconsideration_case_id: str,
    ) -> ReviewCase:
        """对已生效结论提出异议，并开立复议案件；原结论在复议结案前保持有效。"""
        original = self.get(case_id)
        if original.status is not CaseStatus.DECIDED:
            raise ReviewError("只有已生效的结论才能提出异议")
        self._objections.append(Objection(objection_id, case_id, raised_by, reason, raised_on))
        return self.open_case(
            reconsideration_case_id,
            original.applicant_id,
            original.subject_mapping_id,
            raised_on,
            reconsideration_of=case_id,
        )

    def objections_of(self, case_id: str) -> tuple[Objection, ...]:
        return tuple(o for o in self._objections if o.case_id == case_id)

    def pending_reconsiderations(self, applicant_id: str, on: date) -> tuple[ReviewCase, ...]:
        """截至某日仍未结案的复议案件。"""
        return tuple(
            case
            for case in self._cases.values()
            if case.reconsideration_of is not None
            and case.applicant_id == applicant_id
            and case.opened_on <= on
            and (case.decided_on is None or case.decided_on > on)
        )
