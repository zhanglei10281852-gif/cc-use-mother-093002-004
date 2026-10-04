"""端到端冒烟演示：院校证书互认、证据核销、标准升级与历史时点查询。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from skill_recognition import (
    CertificateVersion,
    CaseConclusion,
    Credential,
    EvidenceRecord,
    MappingDecision,
    Mastery,
    Objection,
    PositionDefinition,
    ReconsiderationOutcome,
    RecognitionService,
    Reviewer,
    StandardVersion,
    UnitClaim,
)


def main() -> None:
    svc = RecognitionService(objection_window_days=30)

    # 1) 标准：车工 v1 三个能力单元
    svc.standards.publish(
        StandardVersion("TURNER", "车工国家职业标准", 1, "2018-01-01",
                        frozenset({"U1-识图", "U2-车床操作", "U3-精度检测"}))
    )
    # 2) 院校 A 的"中级"证书版本：同名等级只覆盖 U1/U2，U3 需附加考核
    svc.certificates.publish(
        CertificateVersion(
            "CERT-A", 1, "A院校车工中级证", "A院校", "中级",
            ("TURNER", 1),
            frozenset({
                UnitClaim("U1-识图", Mastery.FULL),
                UnitClaim("U2-车床操作", Mastery.FULL),
            }),
            issue_date="2018-06-01",
        )
    )
    # 3) 岗位锚定 TURNER v1 中级，三项能力全要
    svc.positions.publish(
        PositionDefinition(
            "POS-车工", 1, "装备厂车工（中级岗）", ("TURNER", 1), "中级",
            frozenset({"U1-识图", "U2-车床操作", "U3-精度检测"}),
            valid_from="2018-03-01",
        )
    )
    # 4) 持证人、评审组（含一名应回避的评审人）与案件
    svc.credentials.register(
        Credential("CR-1", "申请人张某", ("CERT-A", 1), "A-2019-001",
                   "2019-03-01", expiry_date="2024-03-01")
    )
    svc.cases.register_reviewer(Reviewer("RV-王", "王工", frozenset({"A院校"})))
    svc.cases.register_reviewer(Reviewer("RV-李", "李工"))
    svc.cases.open_case(
        "CASE-1", "申请人张某", ("CERT-A", 1), ("TURNER", 1), "中级",
        frozenset({"A院校"}), "2019-04-01",
    )
    try:
        svc.cases.assign_reviewer("CASE-1", "RV-王", 1, "2019-04-02")
    except Exception as exc:  # 利益冲突，应回避
        print("拦截冲突评审人:", exc)
    case = svc.cases.assign_reviewer("CASE-1", "RV-李", 1, "2019-04-02")

    # 5) 附加考核通过证据（一次性使用、2020 年底前有效），令牌重复提交幂等
    svc.ledger.register(
        EvidenceRecord("EV-U3", "TURNER", "实操考核记录", holder_id="申请人张某",
                       valid_from="2019-04-10", valid_until="2020-12-31",
                       max_uses=1, units=frozenset({"U3-精度检测"}),
                       standard_key=("TURNER", 1))
    )
    svc.submit_evidence("EV-U3", "CASE-1", "2019-04-10", token="tok-1",
                        purpose="附加考核通过")
    svc.submit_evidence("EV-U3", "CASE-1", "2019-04-10", token="tok-1")  # 重放
    try:
        svc.submit_evidence("EV-U3", "CASE-2", "2019-05-01", token="tok-2")
    except Exception as exc:  # 次数已耗尽，不能靠换案件绕过
        print("拦截重复使用证据:", exc)
    svc.cases.attach_evidence("CASE-1", "EV-U3", case.version, "2019-04-10")
    case = svc.cases.get("CASE-1")

    # 6) 附加考核决定落映射：U1/U2 等同，U3 考核通过后认可
    conclusion = CaseConclusion(
        MappingDecision.ADDITIONAL_ASSESSMENT, "2019-04-15", "2019-05-01",
        covered_units=frozenset({"U1-识图", "U2-车床操作"}),
        assessment_units=frozenset({"U3-精度检测"}),
        rationale="U3 以附加实操考核为准", issued_by="RV-李",
    )
    svc.record_conclusion("CASE-1", conclusion, case.version, "MAP-1")

    view = svc.recognize("申请人张某", "2019-06-01")
    print("2019-06-01 可认可岗位:",
          [p.position.title for p in view.recognizable_positions],
          "依据:", list(view.mapping_basis))

    # 7) 证据过期后历史结论仍可追溯，但岗位不再可认可
    view_expired = svc.recognize("申请人张某", "2021-06-01")
    print("2021-06-01 缺失能力:",
          sorted({u for p in view_expired.positions for u in p.missing_units}))

    # 8) 标准升级：2022 版新增 U4-数控编程，旧映射需复议，持证人需补证
    impact = svc.apply_standard_upgrade(
        StandardVersion(
            "TURNER", "车工国家职业标准", 2, "2022-01-01",
            frozenset({"U1-识图", "U2-车床操作", "U3-精度检测", "U4-数控编程"}),
            predecessor_revision=1,
        ),
        retire_date="2022-06-30",
    )
    print("升级后映射判定:", [(v.mapping.mapping_id, v.verdict, v.reason)
                            for v in impact.verdicts])
    print("受影响持证人:", [(h.holder_id, h.expired, sorted(h.missing_units))
                          for h in impact.affected_holders])

    # 9) 历史时点查询不被升级污染
    view_history = svc.recognize("申请人张某", "2019-06-01")
    print("历史查询仍依据标准版本:", list(view_history.standard_versions_in_force))


if __name__ == "__main__":
    main()
