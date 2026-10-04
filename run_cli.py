"""命令行冒烟：演示职业能力互认服务的核心流程。"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from skill_recognition.catalog import (
    CertificateVersion,
    CompetencyUnit,
    HeldCertificate,
    PositionProfile,
)
from skill_recognition.contracts import StandardVersion
from skill_recognition.evidence import PracticalEvidence
from skill_recognition.mapping import EquivalenceMapping, MappingBasis
from skill_recognition.review import Decision
from skill_recognition.service import RecognitionService


def report_view(report):
    return {
        "申请人": report.applicant_id,
        "查询日期": str(report.as_of),
        "可认可岗位": [p.position_id for p in report.recognizable_positions],
        "部分认可岗位": {
            p.position_id: {"已认可": list(p.recognized_units), "缺失能力": list(p.missing_units)}
            for p in report.partial_positions
        },
        "待复议事项": list(report.pending_reconsiderations),
        "所依据标准版本": [f"{s.display_name} 第{s.revision}版" for s in report.standard_versions],
    }


def main():
    service = RecognitionService()
    steps = []

    # 1. 标准版本与能力单元（第 2 版用 WELD-3B 取代 WELD-3）
    std_v1 = StandardVersion("STD-WELD", "电焊工国家职业标准", 1)
    std_v2 = StandardVersion("STD-WELD", "电焊工国家职业标准", 2)
    service.register_standard(std_v1, (
        CompetencyUnit("WELD-1", "平焊操作", std_v1, ("焊缝外观检测",)),
        CompetencyUnit("WELD-2", "立焊操作", std_v1, ("无损探伤",)),
        CompetencyUnit("WELD-3", "焊接安全规程", std_v1),
    ))
    service.register_standard(std_v2, (
        CompetencyUnit("WELD-1", "平焊操作", std_v2, ("焊缝外观检测",)),
        CompetencyUnit("WELD-2", "立焊操作", std_v2, ("无损探伤",)),
        CompetencyUnit("WELD-3B", "焊接安全与应急处置", std_v2),
    ))

    # 2. 证书版本：同名“四级”等级，不同院校版本的实操覆盖不同
    service.register_certificate_version(
        CertificateVersion("CERT-CN-2020", "某中职院校", "电焊工", "四级", std_v1, ("WELD-1", "WELD-2")))
    service.register_certificate_version(
        CertificateVersion("CERT-ASEAN-2021", "东盟合作院校", "Welding", "四级", std_v1, ("WELD-1",)))
    service.register_certificate_version(
        CertificateVersion("CERT-ADV-2019", "某技师学院", "电焊工", "三级", std_v1, ("WELD-1", "WELD-3")))

    # 3. 互认映射（同名四级映射到不同能力单元集合）
    service.register_mapping(
        EquivalenceMapping("M-CN", MappingBasis.CERTIFICATE, "CERT-CN-2020", std_v1, ("WELD-1", "WELD-2"), date(2024, 1, 1)))
    service.register_mapping(
        EquivalenceMapping("M-ASEAN", MappingBasis.CERTIFICATE, "CERT-ASEAN-2021", std_v1, ("WELD-1",), date(2024, 1, 1)))
    service.register_mapping(
        EquivalenceMapping("M-ADV-1", MappingBasis.CERTIFICATE, "CERT-ADV-2019", std_v1, ("WELD-1",), date(2024, 1, 1)))
    service.register_mapping(
        EquivalenceMapping("M-ADV-3", MappingBasis.CERTIFICATE, "CERT-ADV-2019", std_v1, ("WELD-3",), date(2024, 1, 1)))

    # 4. 岗位画像
    service.register_position(PositionProfile("POS-WELDER", "焊工岗", std_v1, ("WELD-1", "WELD-2")))
    service.register_position(PositionProfile("POS-SAFETY", "焊接安全员", std_v1, ("WELD-1", "WELD-3")))
    service.register_position(PositionProfile("POS-WELDER-2", "焊工岗（2025版）", std_v2, ("WELD-1", "WELD-2")))
    service.register_position(PositionProfile("POS-SAFETY-2", "焊接安全监督岗", std_v2, ("WELD-1", "WELD-3B")))

    # 5. 持证与证据
    service.issue_certificate(HeldCertificate("APP-1", "CERT-CN-2020", date(2024, 2, 1), date(2026, 2, 1)))
    service.issue_certificate(HeldCertificate("APP-2", "CERT-ADV-2019", date(2024, 1, 1), date(2027, 1, 1)))
    service.register_evidence(PracticalEvidence(
        "EV-1", "APP-1", "FP-焊缝试件-001", "焊接试件",
        date(2024, 2, 1), date(2024, 2, 1), date(2025, 12, 31), max_uses=1))
    try:
        service.register_evidence(PracticalEvidence(
            "EV-1B", "APP-1", "FP-焊缝试件-001", "焊接试件", date(2024, 3, 1), date(2024, 3, 1)))
    except ValueError as exc:
        steps.append({"步骤": "同一证据换号重复登记", "结果": f"已拦截：{exc}"})

    # 6. 评审：利益冲突回避、投票、证据提交、决定生效
    service.open_review("CASE-1", "APP-1", "M-CN", date(2024, 3, 1))
    case = service.board.get("CASE-1")
    case.declare_recusal("张老师", "发证院校在职教师，存在利益冲突", date(2024, 3, 1))
    try:
        case.cast_vote("张老师", Decision.EQUIVALENT, "回避后仍投票", date(2024, 3, 2))
    except Exception as exc:
        steps.append({"步骤": "已回避评审人投票", "结果": f"已拦截：{exc}"})
    case.cast_vote("李老师", Decision.EQUIVALENT, "实操覆盖一致", date(2024, 3, 2))
    service.submit_evidence("EV-1", "CASE-1", date(2024, 3, 2))
    seen_version = case.version
    service.decide("CASE-1", Decision.EQUIVALENT, date(2024, 3, 5),
                   expected_version=seen_version, rationale="评审组一致同意")
    try:
        service.decide("CASE-1", Decision.REJECTED, date(2024, 3, 5), expected_version=seen_version)
    except Exception as exc:
        steps.append({"步骤": "并行评审覆盖已生效结论", "结果": f"已拦截：{exc}"})
    steps.append({"步骤": "CASE-1 等同决定生效后查询", "结果": report_view(service.assess("APP-1", date(2024, 6, 1)))})

    # 7. 异议与复议：复议期间原结论保持有效，复议结案后以新结论为准
    service.file_objection("OBJ-1", "CASE-1", "APP-1", "评审程序瑕疵", date(2025, 1, 10), "CASE-2")
    steps.append({"步骤": "异议已提、复议未决", "结果": report_view(service.assess("APP-1", date(2025, 1, 15)))})
    case2 = service.board.get("CASE-2")
    service.decide("CASE-2", Decision.EQUIVALENT, date(2025, 2, 1),
                   expected_version=case2.version, rationale="复议维持等同")
    steps.append({"步骤": "复议结案后查询", "结果": report_view(service.assess("APP-1", date(2025, 2, 2)))})

    # 8. APP-2 按两条映射分别评审
    service.open_review("CASE-3", "APP-2", "M-ADV-1", date(2024, 3, 1))
    service.decide("CASE-3", Decision.EQUIVALENT, date(2024, 3, 5),
                   expected_version=service.board.get("CASE-3").version)
    service.open_review("CASE-4", "APP-2", "M-ADV-3", date(2024, 3, 1))
    service.decide("CASE-4", Decision.EQUIVALENT, date(2024, 3, 5),
                   expected_version=service.board.get("CASE-4").version)
    steps.append({"步骤": "APP-2 升级前查询", "结果": report_view(service.assess("APP-2", date(2025, 6, 1)))})

    # 9. 标准升级：计算影响并执行，历史依据不改写
    carryover = {"WELD-1": "WELD-1", "WELD-2": "WELD-2", "WELD-3": None}
    plan = service.plan_upgrade(std_v1, std_v2, carryover, date(2025, 7, 1))
    steps.append({"步骤": "标准升级影响分析", "结果": {
        "继续有效映射": list(plan.continued_mappings),
        "终止映射": list(plan.terminated_mappings),
        "需补充证明": [
            {"持证人": s.holder_id, "证书": s.cert_id, "缺失单元": list(s.missing_units)}
            for s in plan.supplementary
        ],
    }})
    service.apply_upgrade(plan, carryover)
    steps.append({"步骤": "升级后 APP-1（结论平移）", "结果": report_view(service.assess("APP-1", date(2025, 8, 1)))})
    steps.append({"步骤": "升级后 APP-2（安全规程需补证）", "结果": report_view(service.assess("APP-2", date(2025, 8, 1)))})
    steps.append({"步骤": "历史时点不变：APP-2 升级前", "结果": report_view(service.assess("APP-2", date(2025, 6, 1)))})
    steps.append({"步骤": "证书到期后 APP-1（需补证续期）", "结果": report_view(service.assess("APP-1", date(2026, 3, 1)))})

    print(json.dumps(steps, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
