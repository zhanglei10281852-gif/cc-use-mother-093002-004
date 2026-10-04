import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from skill_recognition import (
    CaseConclusion,
    CONTINUES,
    EvidenceRecord,
    MappingDecision,
    NEEDS_REVIEW,
    Objection,
    ReconsiderationOutcome,
    StandardVersion,
)
from skill_recognition.engine import ASSESSMENT_PASS_PURPOSE

from _fixtures import U1, U2, U3, U4, build_service, open_case


def assess(svc, day):
    view = svc.recognize("张某", day)
    return view


def decide_partial(svc, case_id="CASE-1", on="2019-04-15", effective="2019-05-01",
                   covered=frozenset({U1, U2}), mapping_id="MAP-1",
                   decision=MappingDecision.PARTIAL_EQUIVALENT,
                   assessment=frozenset(), version=None,
                   supersedes_mapping_id="", issued_by="RV-李"):
    v = version if version is not None else svc.cases.get(case_id).version
    return svc.record_conclusion(
        case_id,
        CaseConclusion(decision, on, effective, covered, assessment, "评审意见", issued_by),
        v, mapping_id, supersedes_mapping_id=supersedes_mapping_id,
    )


def assign_li(svc):
    case = svc.cases.assign_reviewer("CASE-1", "RV-李", 1, "2019-04-02")
    return case.version


class RecognitionEngineTests(unittest.TestCase):
    def setUp(self):
        self.svc = build_service()
        open_case(self.svc)
        assign_li(self.svc)

    def test_before_effective_date_nothing_recognized(self):
        decide_partial(self.svc)
        view = assess(self.svc, "2019-04-30")  # 映射 5/1 才生效
        self.assertEqual(view.recognizable_positions, ())
        pos = view.positions[0]
        self.assertEqual(pos.missing_units, frozenset({U1, U2, U3}))

    def test_partial_mapping_reports_missing_unit(self):
        decide_partial(self.svc)  # 仅 U1/U2
        view = assess(self.svc, "2019-06-01")
        self.assertEqual(view.recognizable_positions, ())
        self.assertEqual(view.positions[0].missing_units, frozenset({U3}))
        self.assertEqual(view.positions[0].unit_basis[U1][0].mapping_id, "MAP-1")

    def test_assessment_pass_evidence_unlocks_unit(self):
        decide_partial(
            self.svc,
            decision=MappingDecision.ADDITIONAL_ASSESSMENT,
            covered=frozenset({U1, U2}),
            assessment=frozenset({U3}),
        )
        # 证据尚未提交：U3 缺失
        self.assertEqual(assess(self.svc, "2019-06-01").positions[0].missing_units,
                         frozenset({U3}))
        self.svc.ledger.register(
            EvidenceRecord("EV-U3", "TURNER", "实操考核记录", holder_id="张某",
                           valid_from="2019-06-02", valid_until="2020-12-31",
                           max_uses=1, units=frozenset({U3}),
                           standard_key=("TURNER", 1))
        )
        self.svc.submit_evidence("EV-U3", "CASE-1", "2019-06-02", "tok-1",
                                 purpose=ASSESSMENT_PASS_PURPOSE)
        view = assess(self.svc, "2019-06-03")
        self.assertEqual(view.positions[0].missing_units, frozenset())
        self.assertTrue(view.positions[0].eligible)

    def test_expired_credential_and_evidence_change_view_but_not_history(self):
        decide_partial(self.svc)
        self.svc.ledger.register(
            EvidenceRecord("EV-U3", "TURNER", "实操考核记录", holder_id="张某",
                           valid_from="2019-06-01", valid_until="2020-12-31",
                           max_uses=None, units=frozenset({U3}),
                           standard_key=("TURNER", 1))
        )
        self.svc.submit_evidence("EV-U3", "CASE-1", "2019-06-01", "tok-1")
        self.assertTrue(assess(self.svc, "2019-06-01").positions[0].eligible)
        # 证据过期后
        self.assertEqual(assess(self.svc, "2021-06-01").positions[0].missing_units,
                         frozenset({U3}))
        # 证书也过期后
        self.assertEqual(assess(self.svc, "2025-01-01").positions[0].missing_units,
                         frozenset({U1, U2, U3}))
        # 历史时点仍可原样复原
        self.assertTrue(assess(self.svc, "2019-06-01").positions[0].eligible)

    def test_pending_objection_and_reconsideration_show_in_view(self):
        decide_partial(self.svc)
        v = self.svc.cases.get("CASE-1").version
        self.svc.cases.file_objection(
            "CASE-1", Objection("OBJ-1", "张某", "2019-05-10", "异议"), v
        )
        view = assess(self.svc, "2019-05-12")
        self.assertEqual([m.kind for m in view.pending_matters], ["异议"])
        v = self.svc.cases.get("CASE-1").version
        self.svc.cases.rule_objection(
            "CASE-1", "OBJ-1", True, "RV-赵", v, "2019-05-15",
            reconsideration_id="RECON-1",
        )
        view = assess(self.svc, "2019-05-16")
        self.assertEqual([m.kind for m in view.pending_matters], ["复议"])
        # 更早的日期看不到后来发生的事项
        self.assertEqual(assess(self.svc, "2019-05-09").pending_matters, ())

    def test_reconsideration_revision_supersedes_without_rewriting_history(self):
        decide_partial(self.svc, covered=frozenset({U1}), mapping_id="MAP-1")
        # 2019-06 时 U2/U3 缺失
        self.assertEqual(assess(self.svc, "2019-06-01").positions[0].missing_units,
                         frozenset({U2, U3}))
        # 标准升级之外，评审组自查复议改判：补测后增认 U2
        v = self.svc.cases.get("CASE-1").version
        self.svc.cases.start_reconsideration(
            "CASE-1", "RECON-9", "RV-赵", v, "2019-07-01", "评审组自查"
        )
        v = self.svc.cases.get("CASE-1").version
        revised = CaseConclusion(
            MappingDecision.PARTIAL_EQUIVALENT, "2019-07-05", "2019-08-01",
            frozenset({U1, U2}), frozenset(), "补测合格", "RV-李",
        )
        new_map = self.svc.resolve_reconsideration(
            "CASE-1", "RECON-9", ReconsiderationOutcome.REVISED, "RV-赵", v,
            "2019-07-05", revised=revised, new_mapping_id="MAP-2",
        )
        self.assertEqual(new_map.supersedes, "MAP-1")
        self.assertFalse(self.svc.mappings.get("MAP-1").is_effective_on("2019-08-01"))
        # 新结论生效后 U2 已认可，仅余 U3
        self.assertEqual(assess(self.svc, "2019-08-02").positions[0].missing_units,
                         frozenset({U3}))
        # 改判前的历史查询仍只看到旧映射、旧缺口
        history = assess(self.svc, "2019-06-01")
        self.assertEqual(history.positions[0].missing_units, frozenset({U2, U3}))
        self.assertEqual(history.mapping_basis, ("MAP-1",))

    def test_parallel_cross_case_conclusion_cannot_silently_override(self):
        from skill_recognition import EffectiveConclusionConflict

        # 并行案件 CASE-2 就同一证书版本→同一标准等级先作出生效映射
        self.svc.cases.open_case(
            "CASE-2", "张某", ("CERT-A", 1), ("TURNER", 1), "中级",
            frozenset({"A院校"}), "2019-04-01",
        )
        self.svc.cases.assign_reviewer("CASE-2", "RV-赵", 1, "2019-04-02")
        decide_partial(self.svc, case_id="CASE-2", mapping_id="MAP-X",
                       covered=frozenset({U1, U2}), version=2, issued_by="RV-赵")
        # CASE-1 再作同口径结论：拒绝静默覆盖
        v = self.svc.cases.get("CASE-1").version
        with self.assertRaises(EffectiveConclusionConflict):
            decide_partial(self.svc, case_id="CASE-1", mapping_id="MAP-Y",
                           covered=frozenset({U1}), version=v)
        # 案件状态没有被半生效操作改动，仍可用同一版本号显式替代
        self.assertEqual(self.svc.cases.get("CASE-1").version, v)
        decide_partial(self.svc, case_id="CASE-1", mapping_id="MAP-Y",
                       covered=frozenset({U1}), version=v,
                       supersedes_mapping_id="MAP-X",
                       on="2019-04-20", effective="2019-05-10")
        old = self.svc.mappings.get("MAP-X")
        self.assertFalse(old.is_current())
        self.assertTrue(old.is_effective_on("2019-05-09"))
        self.assertEqual(old.superseded_by, "MAP-Y")

    def test_duplicate_mapping_id_leaves_case_untouched(self):
        from skill_recognition import ValidationError

        decide_partial(self.svc, mapping_id="MAP-DUP", covered=frozenset({U1}))
        # 复议改判时误用已存在的映射编号：案件不应被推进
        v = self.svc.cases.get("CASE-1").version
        self.svc.cases.start_reconsideration(
            "CASE-1", "RECON-D", "RV-赵", v, "2019-07-01", "自查"
        )
        v2 = self.svc.cases.get("CASE-1").version
        revised = CaseConclusion(
            MappingDecision.PARTIAL_EQUIVALENT, "2019-07-05", "2019-08-01",
            frozenset({U1, U2}), frozenset(), "改判", "RV-李",
        )
        with self.assertRaises(ValidationError):
            self.svc.resolve_reconsideration(
                "CASE-1", "RECON-D", ReconsiderationOutcome.REVISED, "RV-赵",
                v2, "2019-07-05", revised=revised, new_mapping_id="MAP-DUP",
            )
        # 仍处于复议中，未被错误终结
        from skill_recognition import CaseStatus
        self.assertEqual(self.svc.cases.get("CASE-1").status, CaseStatus.RECONSIDERING)


class StandardUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.svc = build_service()
        open_case(self.svc)
        assign_li(self.svc)

    def _open_b_case_and_decide_equivalent(self):
        """B 院校证书三单元全覆盖，可合法作出等同结论。"""
        self.svc.cases.open_case(
            "CASE-B", "张某", ("CERT-B", 1), ("TURNER", 1), "中级",
            frozenset({"B院校"}), "2019-04-01",
        )
        v = self.svc.cases.assign_reviewer("CASE-B", "RV-李", 1, "2019-04-02").version
        decide_partial(
            self.svc, case_id="CASE-B",
            decision=MappingDecision.EQUIVALENT,
            covered=frozenset({U1, U2, U3}),
            mapping_id="MAP-B1", version=v,
        )

    def _upgrade(self, units=frozenset({U1, U2, U3, U4}), retire="2022-06-30"):
        return self.svc.apply_standard_upgrade(
            StandardVersion("TURNER", "车工国家职业标准", 2, "2022-01-01",
                            units, predecessor_revision=1),
            retire_date=retire,
        )

    def test_equivalent_mapping_with_added_unit_needs_review(self):
        self._open_b_case_and_decide_equivalent()
        impact = self._upgrade()
        b_verdicts = [v for v in impact.verdicts if v.mapping.mapping_id == "MAP-B1"]
        self.assertEqual(len(b_verdicts), 1)
        verdict = b_verdicts[0]
        self.assertEqual(verdict.verdict, NEEDS_REVIEW)
        self.assertEqual(verdict.added_required_units, frozenset({U4}))
        # B 证持证人需补 U4 证明
        b_holder = next(h for h in impact.affected_holders if h.credential_id == "CR-2")
        self.assertEqual(b_holder.missing_units, frozenset({U4}))
        self.assertIn("POS-车工", impact.positions_anchored_to_old)

    def test_unchanged_units_mapping_continues(self):
        decide_partial(self.svc, covered=frozenset({U1, U2}))
        impact = self._upgrade()
        self.assertEqual(impact.verdicts[0].verdict, CONTINUES)
        carried = self.svc.carry_forward("MAP-1", "MAP-2", "管理员")
        self.assertEqual(carried.target_standard_key, ("TURNER", 2))
        self.assertEqual(carried.covered_units, frozenset({U1, U2}))
        self.assertEqual(carried.supersedes, "MAP-1")
        # 旧映射在废止日前仍可解释历史
        old = self.svc.mappings.get("MAP-1")
        self.assertTrue(old.is_effective_on("2022-06-29"))
        self.assertFalse(old.is_effective_on("2022-06-30"))

    def test_carry_forward_refused_for_needs_review_mapping(self):
        self._open_b_case_and_decide_equivalent()
        self._upgrade()
        with self.assertRaises(ValueError):
            self.svc.carry_forward("MAP-B1", "MAP-B2", "管理员")

    def test_unit_removed_forces_review(self):
        decide_partial(self.svc, covered=frozenset({U1, U2}))
        impact = self._upgrade(units=frozenset({U1, U3, U4}))  # U2 被移除
        self.assertEqual(impact.verdicts[0].verdict, NEEDS_REVIEW)
        self.assertEqual(impact.verdicts[0].lost_units, frozenset({U2}))

    def test_expired_credential_flagged_on_upgrade(self):
        decide_partial(self.svc, covered=frozenset({U1, U2}))
        # 持证人证书 2024-03 到期；在更晚的升级时间点应标记 expired
        impact = self._upgrade(retire="2025-06-30",
                               units=frozenset({U1, U2, U3}))
        self.assertEqual(impact.verdicts[0].verdict, CONTINUES)
        self.assertTrue(impact.affected_holders[0].expired)

    def test_send_to_review_terminates_old_mapping_at_retirement(self):
        self._open_b_case_and_decide_equivalent()
        self._upgrade()
        self.svc.send_mapping_to_review(
            "MAP-B1", "RECON-U", "RV-赵", "2022-02-01", "新标准增加 U4"
        )
        self.assertFalse(self.svc.mappings.get("MAP-B1").is_effective_on("2022-07-01"))
        self.assertTrue(self.svc.mappings.get("MAP-B1").is_effective_on("2022-06-29"))

    def test_history_query_after_upgrade_uses_old_standard(self):
        decide_partial(self.svc, covered=frozenset({U1, U2}))
        self.svc.ledger.register(
            EvidenceRecord("EV-U3", "TURNER", "实操考核记录", holder_id="张某",
                           valid_from="2019-06-01", units=frozenset({U3}),
                           standard_key=("TURNER", 1))
        )
        self.svc.submit_evidence("EV-U3", "CASE-1", "2019-06-01", "tok-1")
        self._upgrade()
        history = assess(self.svc, "2019-06-01")
        self.assertTrue(history.positions[0].eligible)
        self.assertEqual(history.standard_versions_in_force, (("TURNER", 1),))

    def test_mapping_for_one_grade_cannot_satisfy_another_grade(self):
        from skill_recognition import PositionDefinition

        decide_partial(self.svc, covered=frozenset({U1, U2}))
        # 同标准再立一个"高级"岗，能力单元相同，但等级口径不同
        self.svc.positions.publish(
            PositionDefinition(
                "POS-高级车工", 1, "高级车工岗", ("TURNER", 1), "高级",
                frozenset({U1, U2, U3}), valid_from="2018-03-01",
            )
        )
        view = assess(self.svc, "2019-06-01")
        by_id = {p.position.position_id: p for p in view.positions}
        # 中级映射不能满足高级岗：U1/U2 也不出现在高级岗依据里
        self.assertEqual(
            by_id["POS-高级车工"].missing_units, frozenset({U1, U2, U3})
        )
        # 中级岗仍只缺 U3
        self.assertEqual(by_id["POS-车工"].missing_units, frozenset({U3}))
        # 等级无关的实操证据可以补高级岗的缺口
        self.svc.ledger.register(
            EvidenceRecord("EV-ALL", "TURNER", "工作经历证明", holder_id="张某",
                           valid_from="2019-06-05", units=frozenset({U1, U2, U3}),
                           standard_key=("TURNER", 1))
        )
        self.svc.submit_evidence("EV-ALL", "CASE-1", "2019-06-05", "tok-9")
        view = assess(self.svc, "2019-06-06")
        by_id = {p.position.position_id: p for p in view.positions}
        self.assertEqual(by_id["POS-高级车工"].missing_units, frozenset())


class RenewalRuleTests(unittest.TestCase):
    def test_instruction_follows_cert_version_rule_and_expiry(self):
        svc = build_service()
        info = svc.renewal_instruction("CR-1", "2023-01-01")
        self.assertFalse(info.expired)  # 2024-03 才到期
        info = svc.renewal_instruction("CR-1", "2025-01-01")
        self.assertTrue(info.expired)
        # 夹具 A 院校版本未显式设置，默认为不予补证
        from skill_recognition import RenewalRule
        self.assertEqual(info.rule, RenewalRule.NOT_RENEWABLE.value)


if __name__ == "__main__":
    unittest.main()
