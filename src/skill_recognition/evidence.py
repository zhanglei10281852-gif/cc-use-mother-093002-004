"""实操证据登记与使用台账。

同一份实操证据（作品、考核记录、工作经历证明等）有有效期与允许使用次数。
台账记录每一次"证据使用"（提交到某评审案件），保证：

* 证据过期后不得再用于任何互认用途；
* 使用次数耗尽后，重复提交（哪怕换案件、换用途）不能再用；
* 同一提交令牌重放是幂等的，既不会重复扣减，也不会借此获得新次数。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .errors import (
    EvidenceExpiredError,
    EvidenceUsageExhaustedError,
    ValidationError,
)


@dataclass(frozen=True)
class EvidenceRecord:
    """一条已登记的实操证据。

    前三个字段（record_id/entity_id/category）保留基础契约口径；
    valid_until 为 None 表示长期有效；max_uses 为 None 表示不限次数。
    """

    record_id: str
    entity_id: str
    category: str
    holder_id: str = ""
    valid_from: str | None = None
    valid_until: str | None = None
    max_uses: int | None = None
    units: frozenset[str] = frozenset()  # 该证据能直接证明的能力单元
    standard_key: tuple[str, int] | None = None  # 能力单元所属标准版本（防跨标准串用）

    def __post_init__(self) -> None:
        if not self.record_id or not self.entity_id or not self.category:
            raise ValidationError("关联记录信息不完整")
        if self.max_uses is not None and self.max_uses < 0:
            raise ValidationError("最大使用次数不能为负")
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until < self.valid_from
        ):
            raise ValidationError("证据有效期截止日不能早于起始日")

    def is_valid_on(self, day: str) -> bool:
        if self.valid_from is not None and day < self.valid_from:
            return False
        if self.valid_until is not None and day > self.valid_until:
            return False
        return True


@dataclass(frozen=True)
class EvidenceUse:
    record_id: str
    case_id: str
    purpose: str
    day: str
    token: str


class EvidenceLedger:
    """证据台账：登记证据并按提交记录核销次数。"""

    def __init__(self) -> None:
        self._records: dict[str, EvidenceRecord] = {}
        self._uses: list[EvidenceUse] = []
        self._tokens: dict[tuple[str, str], EvidenceUse] = {}

    def register(self, record: EvidenceRecord) -> EvidenceRecord:
        if record.record_id in self._records:
            raise ValidationError(f"证据已登记，不能重复建档: {record.record_id}")
        self._records[record.record_id] = record
        return record

    def get(self, record_id: str) -> EvidenceRecord:
        try:
            return self._records[record_id]
        except KeyError:
            raise ValidationError(f"证据不存在: {record_id}") from None

    def uses_of(self, record_id: str) -> list[EvidenceUse]:
        return [u for u in self._uses if u.record_id == record_id]

    def all_uses(self) -> list[EvidenceUse]:
        return list(self._uses)

    def remaining_uses(self, record_id: str, day: str) -> int | None:
        record = self.get(record_id)
        if not record.is_valid_on(day):
            return 0
        if record.max_uses is None:
            return None
        return max(0, record.max_uses - len(self.uses_of(record_id)))

    def submit_use(
        self,
        record_id: str,
        case_id: str,
        day: str,
        purpose: str = "互认评审",
        token: str = "",
    ) -> EvidenceUse:
        """把证据提交给某案件使用；token 相同的重复提交幂等返回原记录。

        幂等只针对"同一次提交的重放"。换 token 即视为新的一次使用，
        此时若证据已过期或次数耗尽，直接拒绝，不能靠重复提交绕过。
        """
        record = self.get(record_id)
        if not token:
            raise ValidationError("证据提交必须携带提交令牌")
        dedup_key = (record_id, token)
        existing = self._tokens.get(dedup_key)
        if existing is not None:
            if existing.case_id != case_id:
                raise ValidationError("提交令牌已被其他案件使用，不能复用")
            return existing
        if not record.is_valid_on(day):
            raise EvidenceExpiredError(f"证据在 {day} 已过有效期: {record_id}")
        if record.max_uses is not None and len(self.uses_of(record_id)) >= record.max_uses:
            raise EvidenceUsageExhaustedError(f"证据使用次数已耗尽: {record_id}")
        use = EvidenceUse(record_id, case_id, purpose, day, token)
        self._uses.append(use)
        self._tokens[dedup_key] = use
        return use
