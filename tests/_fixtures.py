"""测试夹具：构造一个"车工标准 v1 + A院校中级证书"的最小互认世界。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from skill_recognition import (
    CertificateVersion,
    Credential,
    EvidenceRecord,
    Mastery,
    PositionDefinition,
    RecognitionService,
    Reviewer,
    StandardVersion,
    UnitClaim,
)

U1 = "U1-识图"
U2 = "U2-车床操作"
U3 = "U3-精度检测"
U4 = "U4-数控编程"
TURNER_V1_UNITS = frozenset({U1, U2, U3})


def build_service(objection_window_days: int = 30) -> RecognitionService:
    svc = RecognitionService(objection_window_days=objection_window_days)
    svc.standards.publish(
        StandardVersion("TURNER", "车工国家职业标准", 1, "2018-01-01", TURNER_V1_UNITS)
    )
    svc.certificates.publish(
        CertificateVersion(
            "CERT-A", 1, "A院校车工中级证", "A院校", "中级",
            ("TURNER", 1),
            frozenset({UnitClaim(U1, Mastery.FULL), UnitClaim(U2, Mastery.FULL)}),
            issue_date="2018-06-01",
        )
    )
    # B 院校同名"中级"口径不同：三项能力全部覆盖
    svc.certificates.publish(
        CertificateVersion(
            "CERT-B", 1, "B院校车工中级证", "B院校", "中级",
            ("TURNER", 1),
            frozenset({
                UnitClaim(U1, Mastery.FULL),
                UnitClaim(U2, Mastery.FULL),
                UnitClaim(U3, Mastery.FULL),
            }),
            issue_date="2018-06-01",
        )
    )
    svc.positions.publish(
        PositionDefinition(
            "POS-车工", 1, "装备厂车工（中级岗）", ("TURNER", 1), "中级",
            TURNER_V1_UNITS, valid_from="2018-03-01",
        )
    )
    svc.credentials.register(
        Credential("CR-1", "张某", ("CERT-A", 1), "A-2019-001",
                   "2019-03-01", expiry_date="2024-03-01")
    )
    svc.credentials.register(
        Credential("CR-2", "张某", ("CERT-B", 1), "B-2019-007",
                   "2019-03-01", expiry_date="2024-03-01")
    )
    svc.cases.register_reviewer(Reviewer("RV-王", "王工", frozenset({"A院校"})))
    svc.cases.register_reviewer(Reviewer("RV-李", "李工"))
    svc.cases.register_reviewer(Reviewer("RV-赵", "赵工"))
    return svc


def open_case(svc: RecognitionService, case_id: str = "CASE-1", on: str = "2019-04-01"):
    return svc.cases.open_case(
        case_id, "张某", ("CERT-A", 1), ("TURNER", 1), "中级",
        frozenset({"A院校"}), on,
    )
