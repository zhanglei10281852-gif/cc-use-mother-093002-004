import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from skill_recognition.evidence import EvidenceLedger, PracticalEvidence


class EvidenceLedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = EvidenceLedger()
        self.ledger.register(
            PracticalEvidence(
                "EV-1", "APP-1", "FP-焊缝试件-001", "焊接试件",
                date(2024, 1, 1), date(2024, 1, 1), date(2024, 12, 31), max_uses=2,
            )
        )

    def test_duplicate_fingerprint_rejected(self):
        duplicate = PracticalEvidence(
            "EV-2", "APP-1", "FP-焊缝试件-001", "焊接试件",
            date(2024, 2, 1), date(2024, 2, 1),
        )
        with self.assertRaises(ValueError):
            self.ledger.register(duplicate)

    def test_submit_outside_validity_rejected(self):
        with self.assertRaises(ValueError):
            self.ledger.submit("EV-1", "C-1", date(2025, 1, 1))

    def test_max_uses_enforced_across_cases(self):
        self.ledger.submit("EV-1", "C-1", date(2024, 6, 1))
        self.ledger.submit("EV-1", "C-2", date(2024, 6, 2))
        with self.assertRaises(ValueError):
            self.ledger.submit("EV-1", "C-3", date(2024, 6, 3))

    def test_same_case_resubmission_is_idempotent(self):
        first = self.ledger.submit("EV-1", "C-1", date(2024, 6, 1))
        again = self.ledger.submit("EV-1", "C-1", date(2024, 6, 2))
        self.assertEqual(first, again)
        self.assertEqual(len(self.ledger.uses_of("EV-1")), 1)


if __name__ == "__main__":
    unittest.main()
