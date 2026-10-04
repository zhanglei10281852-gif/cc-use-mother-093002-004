import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from skill_recognition.review import (
    CaseStatus,
    ConcurrencyError,
    ConflictOfInterestError,
    Decision,
    DecisionFinalError,
    ReviewBoard,
    ReviewError,
)


class ReviewCaseTests(unittest.TestCase):
    def setUp(self):
        self.board = ReviewBoard()
        self.case = self.board.open_case("C-1", "APP-1", "M-1", date(2024, 1, 5))

    def test_recused_reviewer_cannot_vote(self):
        self.case.declare_recusal("张老师", "发证院校在职教师", date(2024, 1, 6))
        with self.assertRaises(ConflictOfInterestError):
            self.case.cast_vote("张老师", Decision.EQUIVALENT, "回避后仍投票", date(2024, 1, 7))

    def test_parallel_decision_cannot_override(self):
        self.case.cast_vote("李老师", Decision.EQUIVALENT, "同意", date(2024, 1, 6))
        seen_by_both = self.case.version
        self.case.apply_decision(Decision.EQUIVALENT, date(2024, 1, 10), expected_version=seen_by_both)
        with self.assertRaises(ConcurrencyError):
            self.case.apply_decision(Decision.REJECTED, date(2024, 1, 10), expected_version=seen_by_both)

    def test_decided_case_is_final(self):
        self.case.apply_decision(Decision.EQUIVALENT, date(2024, 1, 10), expected_version=0)
        with self.assertRaises(DecisionFinalError):
            self.case.apply_decision(Decision.PARTIAL, date(2024, 1, 11), expected_version=self.case.version)

    def test_objection_requires_decided_case(self):
        with self.assertRaises(ReviewError):
            self.board.file_objection("O-1", "C-1", "APP-1", "理由", date(2024, 1, 6), "C-2")

    def test_objection_opens_reconsideration_and_pending_is_temporal(self):
        self.case.apply_decision(Decision.EQUIVALENT, date(2024, 1, 10), expected_version=0)
        reconsideration = self.board.file_objection(
            "O-1", "C-1", "APP-1", "评审程序瑕疵", date(2024, 2, 1), "C-2"
        )
        self.assertEqual(reconsideration.reconsideration_of, "C-1")
        # 异议提出前没有待复议事项
        self.assertEqual(self.board.pending_reconsiderations("APP-1", date(2024, 1, 31)), ())
        # 异议提出后、复议结案前有待复议事项
        self.assertEqual(
            [c.case_id for c in self.board.pending_reconsiderations("APP-1", date(2024, 2, 1))],
            ["C-2"],
        )
        reconsideration.apply_decision(Decision.EQUIVALENT, date(2024, 3, 1), expected_version=reconsideration.version)
        self.assertEqual(self.board.pending_reconsiderations("APP-1", date(2024, 3, 1)), ())
        # 复议期间原结论始终有效
        self.assertEqual(self.case.status, CaseStatus.DECIDED)
        # 异议与回避过程留痕
        self.assertEqual(len(self.board.objections_of("C-1")), 1)


if __name__ == "__main__":
    unittest.main()
