import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from skill_recognition.catalog import CertificateVersion, HeldCertificate
from skill_recognition.contracts import StandardVersion
from skill_recognition.mapping import (
    EquivalenceMapping,
    MappingBasis,
    MappingRegistry,
    plan_upgrade,
)


class MappingRegistryTests(unittest.TestCase):
    def setUp(self):
        self.std1 = StandardVersion("STD-WELD", "电焊工国家职业标准", 1)
        self.std2 = StandardVersion("STD-WELD", "电焊工国家职业标准", 2)

    def test_effective_window_and_supersession(self):
        registry = MappingRegistry()
        registry.register(
            EquivalenceMapping("M-1", MappingBasis.CERTIFICATE, "CERT-1", self.std1, ("U-1",), date(2024, 1, 1))
        )
        registry.register(
            EquivalenceMapping(
                "M-2", MappingBasis.CERTIFICATE, "CERT-1", self.std1, ("U-1", "U-2"),
                date(2024, 6, 1), supersedes="M-1",
            )
        )
        self.assertEqual([m.mapping_id for m in registry.effective_on(date(2024, 5, 31))], ["M-1"])
        self.assertEqual([m.mapping_id for m in registry.effective_on(date(2024, 6, 1))], ["M-2"])

    def test_retire_keeps_history(self):
        registry = MappingRegistry()
        registry.register(
            EquivalenceMapping("M-1", MappingBasis.CERTIFICATE, "CERT-1", self.std1, ("U-1",), date(2024, 1, 1))
        )
        registry.retire("M-1", date(2024, 9, 1))
        self.assertEqual(len(registry.effective_on(date(2024, 8, 31))), 1)
        self.assertEqual(len(registry.effective_on(date(2024, 9, 1))), 0)

    def test_same_level_name_does_not_imply_same_coverage(self):
        # 同名等级不代表相同实操能力：互认以证书版本为键，而非等级名称
        cn = CertificateVersion("CERT-CN", "院校甲", "电焊工", "四级", self.std1, ("U-1", "U-2"))
        asean = CertificateVersion("CERT-AS", "院校乙", "Welding", "四级", self.std1, ("U-1",))
        self.assertEqual(cn.level, asean.level)
        self.assertNotEqual(cn.covers_units, asean.covers_units)


class UpgradePlanTests(unittest.TestCase):
    def setUp(self):
        self.std1 = StandardVersion("STD-WELD", "电焊工国家职业标准", 1)
        self.std2 = StandardVersion("STD-WELD", "电焊工国家职业标准", 2)

    def test_plan_upgrade_computes_impact(self):
        keep = EquivalenceMapping("M-1", MappingBasis.CERTIFICATE, "CERT-A", self.std1, ("U-1", "U-2"), date(2024, 1, 1))
        drop = EquivalenceMapping("M-2", MappingBasis.CERTIFICATE, "CERT-A", self.std1, ("U-3",), date(2024, 1, 1))
        held = (HeldCertificate("APP-1", "CERT-A", date(2024, 1, 1), date(2026, 1, 1)),)
        certs = (CertificateVersion("CERT-A", "院校", "电焊工", "四级", self.std1, ("U-1", "U-3")),)
        plan = plan_upgrade(
            self.std1, self.std2,
            {"U-1": "U-1", "U-2": "U-2", "U-3": None},
            (keep, drop), held, certs, date(2025, 1, 1),
        )
        self.assertEqual(plan.continued_mappings, ("M-1",))
        self.assertEqual(plan.terminated_mappings, ("M-2",))
        self.assertEqual(len(plan.supplementary), 1)
        self.assertEqual(plan.supplementary[0].holder_id, "APP-1")
        self.assertEqual(plan.supplementary[0].missing_units, ("U-3",))

    def test_expired_certificate_not_in_supplementary(self):
        held = (HeldCertificate("APP-1", "CERT-A", date(2020, 1, 1), date(2024, 1, 1)),)
        certs = (CertificateVersion("CERT-A", "院校", "电焊工", "四级", self.std1, ("U-3",)),)
        plan = plan_upgrade(
            self.std1, self.std2, {"U-3": None}, (), held, certs, date(2025, 1, 1)
        )
        self.assertEqual(plan.supplementary, ())

    def test_plan_upgrade_rejects_wrong_lineage_or_revision(self):
        with self.assertRaises(ValueError):
            plan_upgrade(
                self.std1, StandardVersion("STD-OTHER", "其他标准", 2),
                {}, (), (), (), date(2025, 1, 1),
            )
        with self.assertRaises(ValueError):
            plan_upgrade(self.std2, self.std1, {}, (), (), (), date(2025, 1, 1))


if __name__ == "__main__":
    unittest.main()
