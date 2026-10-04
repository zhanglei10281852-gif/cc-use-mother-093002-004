import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from skill_recognition.errors import (
    CaseStateError,
    ConcurrentReviewError,
    ReviewerConflictError,
)
from skill_recognition.mappings import MappingDecision
from skill_recognition.review import (
    CaseConclusion,
    CaseStatus,
    Objection,
    ObjectionStatus,
    ReconsiderationOutcome,
    Reviewer,
)

from _fixtures import U1, U2, U3, build_service, open_case


def conclusion(decision=MappingDecision.PARTIAL_EQUIVALENT, on="2019-04-15",
               effective="2019-05-01", covered=frozenset({U1}),
               assessment=frozenset(), issued_by="RV-李", note="评审"):
    return CaseConclusion(decision, on, effective, covered, assessment, note, issued_by)


class PanelAndConflictTests(unittest.TestCase):
    def setUp(self):
        self.svc = build_service()
        open_case(self.svc)

    def test_conflicted_reviewer_is_blocked(self):
        with self.assertRaises(ReviewerConflictError):
            self.svc.cases.assign_reviewer("CASE-1", "RV-王", 1, "2019-04-02")

    def test_self_declared_recusal_removes_reviewer_from_active_panel(self):
        case = self.svc.cases.assign_reviewer("CASE-1", "RV-李", 1, "2019-04-02")
        case = self.svc.cases.assign_reviewer("CASE-1", "RV-赵", case.version, "2019-04-03")
        case = self.svc.cases.declare_conflict(
            "CASE-1", "RV-赵", "某申请人亲属", "主动申报", case.version, "2019-04-04"
        )
        self.assertEqual(case.active_panel(), ["RV-李"])
        # 已回避者不能作结论
        with self.assertRaises((ReviewerConflictError, CaseStateError)):
            self.svc.cases.issue_conclusion(
                "CASE-1", conclusion(issued_by="RV-赵"), case.version
            )

    def test_stale_version_is_rejected(self):
        self.svc.cases.assign_reviewer("CASE-1", "RV-李", 1, "2019-04-02")
        # 两个并行评审人都持有 version=2
        with self.assertRaises(ConcurrentReviewError):
            self.svc.cases.attach_evidence("CASE-1", "EV-X", 2, "2019-04-05")
            self.svc.cases.attach_evidence("CASE-1", "EV-Y", 2, "2019-04-05")

    def test_effective_conclusion_cannot_be_overwritten_in_parallel(self):
        case = self.svc.cases.assign_reviewer("CASE-1", "RV-李", 1, "2019-04-02")
        v = case.version
        self.svc.cases.issue_conclusion("CASE-1", conclusion(), v)
        # 旧版本句柄再提交结论：乐观锁拒绝
        with self.assertRaises(ConcurrentReviewError):
            self.svc.cases.issue_conclusion("CASE-1", conclusion(), v)
        # 已生效状态也不允许直接再作结论
        fresh = self.svc.cases.get("CASE-1")
        with self.assertRaises(CaseStateError):
            self.svc.cases.issue_conclusion("CASE-1", conclusion(), fresh.version)
        self.assertEqual(self.svc.cases.get("CASE-1").status, CaseStatus.DECIDED)


class ObjectionAndReconsiderationTests(unittest.TestCase):
    def setUp(self):
        self.svc = build_service()
        open_case(self.svc)
        case = self.svc.cases.assign_reviewer("CASE-1", "RV-李", 1, "2019-04-02")
        self.svc.cases.issue_conclusion("CASE-1", conclusion(), case.version)
        self.decided_version = self.svc.cases.get("CASE-1").version

    def test_objection_within_window_enters_objected(self):
        case = self.svc.cases.file_objection(
            "CASE-1", Objection("OBJ-1", "张某", "2019-05-10", "U1 考核口径有异议"),
            self.decided_version,
        )
        self.assertEqual(case.status, CaseStatus.OBJECTED)

    def test_objection_after_window_rejected(self):
        with self.assertRaises(CaseStateError):
            self.svc.cases.file_objection(
                "CASE-1", Objection("OBJ-2", "张某", "2019-06-01", "超期异议"),
                self.decided_version,
            )

    def test_rejected_objection_returns_to_decided(self):
        self.svc.cases.file_objection(
            "CASE-1", Objection("OBJ-1", "张某", "2019-05-10", "异议"),
            self.decided_version,
        )
        v = self.svc.cases.get("CASE-1").version
        case = self.svc.cases.rule_objection(
            "CASE-1", "OBJ-1", False, "RV-赵", v, "2019-05-15", "理由不成立"
        )
        self.assertEqual(case.status, CaseStatus.DECIDED)
        self.assertEqual(case.objections[0].status, ObjectionStatus.REJECTED)

    def test_accepted_objection_then_uphold_closes_case(self):
        self.svc.cases.file_objection(
            "CASE-1", Objection("OBJ-1", "张某", "2019-05-10", "异议"),
            self.decided_version,
        )
        v = self.svc.cases.get("CASE-1").version
        case = self.svc.cases.rule_objection(
            "CASE-1", "OBJ-1", True, "RV-赵", v, "2019-05-15",
            note="受理", reconsideration_id="RECON-1",
        )
        self.assertEqual(case.status, CaseStatus.RECONSIDERING)
        v = case.version
        case = self.svc.cases.complete_reconsideration(
            "CASE-1", "RECON-1", ReconsiderationOutcome.ORIGINAL_UPHELD,
            "RV-李", v, "2019-05-20",
        )
        self.assertEqual(case.status, CaseStatus.CLOSED)

    def test_conflicted_reviewer_cannot_rule_objection(self):
        self.svc.cases.file_objection(
            "CASE-1", Objection("OBJ-1", "张某", "2019-05-10", "异议"),
            self.decided_version,
        )
        v = self.svc.cases.get("CASE-1").version
        with self.assertRaises(ReviewerConflictError):
            self.svc.cases.rule_objection(
                "CASE-1", "OBJ-1", False, "RV-王", v, "2019-05-15"
            )


if __name__ == "__main__":
    unittest.main()
