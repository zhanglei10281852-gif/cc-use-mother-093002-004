"""证书版本与持证人证书实例。

合作院校颁发的中文证书/职业技能证书分属不同"工种版本"与"等级口径"：
同名等级（如"中级"）在不同版本中覆盖的能力单元不同，因此证书版本必须
显式声明它所依据的标准版本与实际覆盖的能力单元，等级名称只作显示用途。

旧证书到期后的补证规则按证书版本登记（不可补证 / 补交实操证据 /
重新互认评审），补证不改变原证书已形成的历史依据。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .errors import ValidationError


class RenewalRule(str, Enum):
    """旧证书到期后的一致补证规则。"""

    NOT_RENEWABLE = "不予补证"
    SUPPLEMENT_EVIDENCE = "补交实操证据后换发"
    FULL_RE_REVIEW = "重新互认评审"


class Mastery(str, Enum):
    """证书对某能力单元的掌握口径。"""

    FULL = "完全覆盖"
    PARTIAL = "部分覆盖"


@dataclass(frozen=True)
class UnitClaim:
    unit_code: str
    mastery: Mastery

    def __post_init__(self) -> None:
        if not self.unit_code:
            raise ValidationError("能力单元编码不能为空")


@dataclass(frozen=True)
class CertificateVersion:
    """某院校某工种某等级口径的一个证书版本。"""

    cert_family_id: str
    revision: int
    title: str
    issuer: str
    grade_label: str
    standard_key: tuple[str, int]
    claims: frozenset[UnitClaim] = field(default_factory=frozenset)
    issue_date: str = ""
    retire_date: str | None = None
    renewal_rule: RenewalRule = RenewalRule.NOT_RENEWABLE
    default_validity_years: int | None = None  # None 表示长期有效

    def __post_init__(self) -> None:
        if not self.cert_family_id or self.revision < 1:
            raise ValidationError("证书版本标识不合法")
        if not self.title or not self.issuer or not self.grade_label:
            raise ValidationError("证书名称、颁发方与等级口径不能为空")
        if not self.issue_date:
            raise ValidationError("证书版本必须有启用日期")
        if self.default_validity_years is not None and self.default_validity_years < 1:
            raise ValidationError("有效期年限必须为正")

    @property
    def key(self) -> tuple[str, int]:
        return (self.cert_family_id, self.revision)

    @property
    def covered_units(self) -> frozenset[str]:
        return frozenset(c.unit_code for c in self.claims)

    def claim_for(self, unit_code: str) -> UnitClaim | None:
        return next((c for c in self.claims if c.unit_code == unit_code), None)

    def is_active_on(self, day: str) -> bool:
        if day < self.issue_date:
            return False
        return self.retire_date is None or day < self.retire_date


@dataclass(frozen=True)
class Credential:
    """持证人实际持有的一张证书（由某院校按某版本颁发）。"""

    credential_id: str
    holder_id: str
    cert_key: tuple[str, int]
    serial: str
    issue_date: str
    expiry_date: str | None = None  # None 表示长期有效

    def __post_init__(self) -> None:
        if not self.credential_id or not self.holder_id or not self.serial:
            raise ValidationError("持证记录信息不完整")
        if not self.issue_date:
            raise ValidationError("证书必须有颁发日期")
        if self.expiry_date is not None and self.expiry_date < self.issue_date:
            raise ValidationError("到期日期不能早于颁发日期")

    def is_valid_on(self, day: str) -> bool:
        """证书实例在指定日期是否处于有效期内（含到期当日）。"""
        if day < self.issue_date:
            return False
        return self.expiry_date is None or day <= self.expiry_date


class CertificateCatalog:
    """证书版本目录：登记各院校各工种版本与等级口径。"""

    def __init__(self) -> None:
        self._versions: dict[tuple[str, int], CertificateVersion] = {}

    def publish(self, version: CertificateVersion) -> CertificateVersion:
        if version.key in self._versions:
            raise ValidationError(f"证书版本已存在: {version.key}")
        self._versions[version.key] = version
        return version

    def get(self, cert_family_id: str, revision: int) -> CertificateVersion:
        try:
            return self._versions[(cert_family_id, revision)]
        except KeyError:
            raise ValidationError(f"证书版本不存在: {(cert_family_id, revision)}") from None

    def all(self) -> list[CertificateVersion]:
        return list(self._versions.values())


class CredentialRegistry:
    """持证人证书登记簿。"""

    def __init__(self) -> None:
        self._credentials: dict[str, Credential] = {}

    def register(self, credential: Credential) -> Credential:
        if credential.credential_id in self._credentials:
            raise ValidationError(f"持证记录已存在: {credential.credential_id}")
        return self._credentials.setdefault(credential.credential_id, credential)

    def get(self, credential_id: str) -> Credential:
        try:
            return self._credentials[credential_id]
        except KeyError:
            raise ValidationError(f"持证记录不存在: {credential_id}") from None

    def held_by(self, holder_id: str) -> list[Credential]:
        return [c for c in self._credentials.values() if c.holder_id == holder_id]

    def all_ids(self) -> list[str]:
        return list(self._credentials.keys())

    def valid_for_on(self, holder_id: str, day: str) -> list[Credential]:
        return [
            c
            for c in self.held_by(holder_id)
            if c.is_valid_on(day)
        ]
