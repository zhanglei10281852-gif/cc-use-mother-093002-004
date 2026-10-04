import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from skill_recognition.catalog import CertificateVersion, CompetencyUnit, HeldCertificate, PositionProfile
from skill_recognition.contracts import StandardVersion
from skill_recognition.mapping import EquivalenceMapping, MappingBasis
from skill_recognition.review import Decision, ReviewError
from skill_recognition.service import RecognitionService


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = RecognitionService()
        self.std1 = StandardVersion("STD-WELD", "电焊工国家职业标准", 1)
        self.std2 = StandardVersion("STD-WELD", "电焊工国家职业标准", 2)
        s = self.service
        s.register_standard(self.std1, (
            CompetencyUnit("WELD-1", "平焊操作", self.std1),
            CompetencyUnit("WELD-2", "立焊操作", self.std1),
            CompetencyUnit("WELD-3", "焊接安全规程", self.std1),
        ))
        s.register_standard(self.std2, (
            CompetencyUnit("WELD-1", "平焊操作", self.std2),
            CompetencyUnit("WELD-2", "立焊操作", self.std2),
            CompetencyUnit("WELD-3B", "焊接安全与应急处置", self.std2),
        ))
        s.register_certificate_version(
            CertificateVersion("CERT-CN", "中职院校", "电焊工", "四级", self.std1, ("WELD-1", "WELD-2"))
        )
        s.register_certificate_version(
            CertificateVersion("CERT-ADV", "技师学院", "电焊工", "三级", self.std1, ("WELD-1", "WELD-3"))
        )
        s.issue_certificate(HeldCertificate("APP-1", "CERT-CN", date(2024, 2, 1), date(2026, 2, 1)))
        s.issue_certificate(HeldCertificate("APP-2", "CERT-ADV", date(2024, 1, 1), date(2027, 1, 1)))
        s.register_mapping(
            EquivalenceMapping("M-CN", MappingBasis.CERTIFICATE, "CERT-CN", self.std1, ("WELD-1", "WELD-2"), date(2024, 1, 1))
        )
        s.register_mapping(
            EquivalenceMapping("M-ADV-1", MappingBasis.CERTIFICATE, "CERT-ADV", self.std1, ("WELD-1",), date(2024, 1, 1))
        )
        s.register_mapping(
            EquivalenceMapping("M-ADV-3", MappingBasis.CERTIFICATE, "CERT-ADV", self.std1, ("WELD-3",), date(2024, 1, 1))
        )
        s.register_position(PositionProfile("POS-WELDER", "焊工岗", self.std1, ("WELD-1", "WELD-2")))
        s.register_position(PositionProfile("POS-SAFETY", "焊接安全员", self.std1, ("WELD-1", "WELD-3")))
        s.register_position(PositionProfile("POS-SAFETY-2", "焊接安全监督岗", self.std2, ("WELD-1", "WELD-3B")))

    def _decide(self, case_id, applicant, mapping, on, decision=Decision.EQUIVALENT, **kwargs):
        self.service.open_review(case_id, applicant, mapping, on)
        case = self.service.board.get(case_id)
        return self.service.decide(case_id, decision, on, expected_version=case.version, **kwargs)

    def test_full_flow_and_as_of_report(self):
        recognition = self._decide("C-1", "APP-1", "M-CN", date(2024, 3, 1))
        # 结论有效期不超过证书有效期：旧证书到期后需按统一规则补证
        self.assertEqual(recognition.valid_to, date(2026, 2, 1))
        report = self.service.assess("APP-1", date(2024, 6, 1))
        self.assertEqual([p.position_id for p in report.recognizable_positions], ["POS-WELDER"])
        self.assertEqual(report.standard_versions, (self.std1,))
        self.assertEqual(report.pending_reconsiderations, ())

    def test_recognition_expires_with_certificate(self):
        self._decide("C-1", "APP-1", "M-CN", date(2024, 3, 1))
        report = self.service.assess("APP-1", date(2026, 3, 1))
        self.assertEqual(report.recognizable_positions, ())
        self.assertEqual(report.partial_positions, ())

    def test_expired_certificate_cannot_be_recognized(self):
        self.service.open_review("C-8", "APP-1", "M-CN", date(2026, 3, 1))
        case = self.service.board.get("C-8")
        with self.assertRaises(ReviewError):
            self.service.decide("C-8", Decision.EQUIVALENT, date(2026, 3, 2), expected_version=case.version)

    def test_parallel_decision_cannot_override(self):
        self.service.open_review("C-1", "APP-1", "M-CN", date(2024, 3, 1))
        case = self.service.board.get("C-1")
        seen_by_both = case.version
        self.service.decide("C-1", Decision.EQUIVALENT, date(2024, 3, 2), expected_version=seen_by_both)
        with self.assertRaises(ReviewError):
            self.service.decide("C-1", Decision.REJECTED, date(2024, 3, 2), expected_version=seen_by_both)

    def test_decision_unit_validation(self):
        self.service.open_review("C-9", "APP-1", "M-CN", date(2024, 3, 1))
        case = self.service.board.get("C-9")
        with self.assertRaises(ReviewError):  # 等同不能漏单元
            self.service.decide("C-9", Decision.EQUIVALENT, date(2024, 3, 2),
                                expected_version=case.version, recognized_units=("WELD-1",))
        with self.assertRaises(ReviewError):  # 部分等同不能覆盖全部
            self.service.decide("C-9", Decision.PARTIAL, date(2024, 3, 2),
                                expected_version=case.version, recognized_units=("WELD-1", "WELD-2"))
        with self.assertRaises(ReviewError):  # 不能超出映射范围
            self.service.decide("C-9", Decision.PARTIAL, date(2024, 3, 2),
                                expected_version=case.version, recognized_units=("WELD-1", "WELD-3"))

    def test_objection_reconsideration_replaces_without_rewriting(self):
        self._decide("C-1", "APP-1", "M-CN", date(2024, 3, 1))
        self.service.file_objection("O-1", "C-1", "APP-1", "评审程序瑕疵", date(2025, 1, 10), "C-2")
        # 复议未决：待复议事项出现，原结论仍有效
        report = self.service.assess("APP-1", date(2025, 1, 15))
        self.assertEqual(report.pending_reconsiderations, ("C-2",))
        self.assertEqual([p.position_id for p in report.recognizable_positions], ["POS-WELDER"])
        # 复议结论为部分等同
        case2 = self.service.board.get("C-2")
        self.service.decide("C-2", Decision.PARTIAL, date(2025, 2, 1),
                            expected_version=case2.version, recognized_units=("WELD-1",))
        report = self.service.assess("APP-1", date(2025, 2, 2))
        self.assertEqual(report.recognizable_positions, ())
        welder = next(p for p in report.partial_positions if p.position_id == "POS-WELDER")
        self.assertEqual(welder.missing_units, ("WELD-2",))
        self.assertEqual(report.pending_reconsiderations, ())
        # 历史时点不变：复议前的查询结果保持等同
        history = self.service.assess("APP-1", date(2024, 6, 1))
        self.assertEqual([p.position_id for p in history.recognizable_positions], ["POS-WELDER"])

    def test_upgrade_carries_and_lapses_without_rewriting_history(self):
        self._decide("C-1", "APP-1", "M-CN", date(2024, 3, 1))
        self._decide("C-2", "APP-2", "M-ADV-1", date(2024, 3, 1))
        self._decide("C-3", "APP-2", "M-ADV-3", date(2024, 3, 1))
        carryover = {"WELD-1": "WELD-1", "WELD-2": "WELD-2", "WELD-3": None}
        plan = self.service.plan_upgrade(self.std1, self.std2, carryover, date(2025, 7, 1))
        self.assertEqual(set(plan.continued_mappings), {"M-CN", "M-ADV-1"})
        self.assertEqual(plan.terminated_mappings, ("M-ADV-3",))
        self.assertEqual(len(plan.supplementary), 1)
        self.assertEqual(plan.supplementary[0].holder_id, "APP-2")
        self.assertEqual(plan.supplementary[0].missing_units, ("WELD-3",))

        self.service.apply_upgrade(plan, carryover)
        # 升级后：APP-1 的结论平移到新标准版本
        after = self.service.assess("APP-1", date(2025, 8, 1))
        self.assertEqual(after.standard_versions, (self.std2,))
        # APP-2 的安全规程结论随映射终止而失效，需补充证明
        after2 = self.service.assess("APP-2", date(2025, 8, 1))
        self.assertEqual(after2.recognizable_positions, ())
        safety2 = next(p for p in after2.partial_positions if p.position_id == "POS-SAFETY-2")
        self.assertEqual(safety2.missing_units, ("WELD-3B",))
        # 历史招聘依据不被改写：升级前 APP-2 可任焊接安全员
        before = self.service.assess("APP-2", date(2025, 6, 1))
        self.assertEqual([p.position_id for p in before.recognizable_positions], ["POS-SAFETY"])
        self.assertEqual(before.standard_versions, (self.std1,))


if __name__ == "__main__":
    unittest.main()
