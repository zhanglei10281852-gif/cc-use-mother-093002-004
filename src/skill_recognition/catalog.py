"""标准目录：能力单元、证书版本、持证记录、课程成果与岗位画像。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .contracts import StandardVersion


@dataclass(frozen=True)
class CompetencyUnit:
    """能力单元：挂在某一标准版本下的最小能力粒度，可声明实操要求。"""

    unit_code: str
    title: str
    standard: StandardVersion
    practical_requirements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.unit_code or not self.title:
            raise ValueError("能力单元信息不完整")


@dataclass(frozen=True)
class CertificateVersion:
    """证书版本：某机构按特定工种版本与等级口径颁发的证书模板。

    同名等级不代表相同实操能力，互认一律以证书版本（cert_id）为键，
    而不是以等级名称为键。
    """

    cert_id: str
    issuer: str
    occupation_code: str  # 工种版本
    level: str  # 等级口径
    standard: StandardVersion
    covers_units: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.cert_id or not self.issuer or not self.occupation_code or not self.level:
            raise ValueError("证书版本信息不完整")
        if not self.covers_units:
            raise ValueError("证书版本必须覆盖至少一个能力单元")


@dataclass(frozen=True)
class HeldCertificate:
    """持证人手中的证书实例，带签发日与到期日。"""

    holder_id: str
    cert_id: str
    issued_on: date
    expires_on: date | None = None

    def __post_init__(self) -> None:
        if not self.holder_id or not self.cert_id:
            raise ValueError("持证信息不完整")
        if self.expires_on is not None and self.expires_on < self.issued_on:
            raise ValueError("证书有效期不合法")

    def valid_on(self, day: date) -> bool:
        return self.issued_on <= day and (self.expires_on is None or day <= self.expires_on)


@dataclass(frozen=True)
class CourseOutcome:
    """课程成果：某持证人完成课程后获得的能力单元覆盖。"""

    course_id: str
    provider: str
    holder_id: str
    covers_units: tuple[str, ...]
    completed_on: date

    def __post_init__(self) -> None:
        if not self.course_id or not self.provider or not self.holder_id or not self.covers_units:
            raise ValueError("课程成果信息不完整")


@dataclass(frozen=True)
class PositionProfile:
    """岗位画像：某岗位在特定标准版本下要求的能力单元集合。"""

    position_id: str
    title: str
    standard: StandardVersion
    required_units: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.position_id or not self.title or not self.required_units:
            raise ValueError("岗位画像信息不完整")
