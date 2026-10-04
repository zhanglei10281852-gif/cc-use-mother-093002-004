import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from skill_recognition.evidence import EvidenceLedger, EvidenceRecord
from skill_recognition.errors import (
    EvidenceExpiredError,
    EvidenceUsageExhaustedError,
    ValidationError,
)


def make_record(**over) -> EvidenceRecord:
    params = dict(
        record_id="EV-1",
        entity_id="TURNER",
        category="实操考核记录",
        holder_id="张某",
        valid_from="2019-01-01",
        valid_until="2019-12-31",
        max_uses=2,
    )
    params.update(over)
    return EvidenceRecord(**params)


class EvidenceLedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = EvidenceLedger()
        self.ledger.register(make_record())

    def test_idempotent_same_token_does_not_consume_second_use(self):
        first = self.ledger.submit_use("EV-1", "CASE-1", "2019-03-01", token="T1")
        replay = self.ledger.submit_use("EV-1", "CASE-1", "2019-03-01", token="T1")
        self.assertIs(first, replay)
        self.assertEqual(len(self.ledger.uses_of("EV-1")), 1)

    def test_token_cannot_be_replayed_against_another_case(self):
        self.ledger.submit_use("EV-1", "CASE-1", "2019-03-01", token="T1")
        with self.assertRaises(ValidationError):
            self.ledger.submit_use("EV-1", "CASE-2", "2019-03-01", token="T1")

    def test_repeated_submission_cannot_bypass_use_limit(self):
        self.ledger.submit_use("EV-1", "CASE-1", "2019-03-01", token="T1")
        self.ledger.submit_use("EV-1", "CASE-2", "2019-04-01", token="T2")
        self.assertEqual(self.ledger.remaining_uses("EV-1", "2019-05-01"), 0)
        # 换新案件、新令牌也不能再用
        with self.assertRaises(EvidenceUsageExhaustedError):
            self.ledger.submit_use("EV-1", "CASE-3", "2019-05-01", token="T3")

    def test_expired_evidence_is_rejected_even_with_uses_left(self):
        ledger = EvidenceLedger()
        ledger.register(make_record(record_id="EV-2", max_uses=5))
        with self.assertRaises(EvidenceExpiredError):
            ledger.submit_use("EV-2", "CASE-1", "2020-01-01", token="T1")

    def test_submission_before_validity_start_rejected(self):
        with self.assertRaises(EvidenceExpiredError):
            self.ledger.submit_use("EV-1", "CASE-1", "2018-12-31", token="T1")

    def test_unlimited_uses_still_respects_expiry(self):
        ledger = EvidenceLedger()
        ledger.register(make_record(record_id="EV-3", max_uses=None))
        for day, ok in [("2019-06-01", True), ("2020-01-01", False)]:
            if ok:
                ledger.submit_use("EV-3", f"C-{day}", day, token=f"T-{day}")
            else:
                with self.assertRaises(EvidenceExpiredError):
                    ledger.submit_use("EV-3", "C-X", day, token=f"T-{day}")

    def test_duplicate_registration_rejected(self):
        with self.assertRaises(ValidationError):
            self.ledger.register(make_record())

    def test_submit_requires_token(self):
        with self.assertRaises(ValidationError):
            self.ledger.submit_use("EV-1", "CASE-1", "2019-03-01", token="")


if __name__ == "__main__":
    unittest.main()
