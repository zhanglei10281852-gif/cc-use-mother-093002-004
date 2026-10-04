import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from skill_recognition.errors import StandardLineageError, ValidationError
from skill_recognition.standards import StandardRegistry, StandardVersion

V1 = ("STD", 1)
V2 = ("STD", 2)


class StandardLineageTests(unittest.TestCase):
    def setUp(self):
        self.reg = StandardRegistry()
        self.reg.publish(StandardVersion("STD", "标准", 1, "2018-01-01", frozenset({"A"})))

    def test_published_version_is_immutable(self):
        with self.assertRaises(ValidationError):
            self.reg.publish(StandardVersion("STD", "标准", 1, "2019-01-01"))

    def test_successor_adds_retirement_without_rewriting_old(self):
        old = self.reg.get(*V1)
        self.reg.publish_successor(
            StandardVersion("STD", "标准", 2, "2022-01-01", frozenset({"A", "B"}),
                            predecessor_revision=1),
            retire_date="2022-06-30",
        )
        # 旧版本对象本身没有被改写
        self.assertIsNone(old.retire_date)
        # 废止事实单独登记
        self.assertEqual(self.reg.retirement_of(V1).retire_date, "2022-06-30")
        self.assertEqual(self.reg.retirement_of(V1).successor_key, V2)
        # 日期口径：废止当日旧版失效
        self.assertTrue(self.reg.is_effective_on(V1, "2022-06-29"))
        self.assertFalse(self.reg.is_effective_on(V1, "2022-06-30"))
        self.assertTrue(self.reg.is_effective_on(V2, "2022-06-30"))

    def test_no_double_upgrade_or_fork(self):
        self.reg.publish_successor(
            StandardVersion("STD", "标准", 2, "2022-01-01", predecessor_revision=1),
            "2022-06-30",
        )
        with self.assertRaises(StandardLineageError):
            self.reg.publish_successor(
                StandardVersion("STD", "标准", 3, "2026-01-01", predecessor_revision=1),
                "2026-06-30",
            )

    def test_successor_requires_existing_predecessor(self):
        with self.assertRaises(StandardLineageError):
            self.reg.publish_successor(
                StandardVersion("STD", "标准", 9, "2026-01-01", predecessor_revision=8),
                "2026-06-30",
            )

    def test_lineage_chain_order(self):
        self.reg.publish_successor(
            StandardVersion("STD", "标准", 2, "2022-01-01", predecessor_revision=1),
            "2022-06-30",
        )
        self.reg.publish_successor(
            StandardVersion("STD", "标准", 3, "2026-01-01", predecessor_revision=2),
            "2026-06-30",
        )
        self.assertEqual([v.revision for v in self.reg.lineage("STD")], [1, 2, 3])
        self.assertEqual(self.reg.successor_map("STD"), {V1: V2, V2: ("STD", 3)})

    def test_effective_version_resolves_single_version_per_day(self):
        self.reg.publish_successor(
            StandardVersion("STD", "标准", 2, "2022-01-01", predecessor_revision=1),
            "2022-06-30",
        )
        self.assertEqual(self.reg.effective_version_on("STD", "2021-01-01").revision, 1)
        self.assertEqual(self.reg.effective_version_on("STD", "2023-01-01").revision, 2)


if __name__ == "__main__":
    unittest.main()
