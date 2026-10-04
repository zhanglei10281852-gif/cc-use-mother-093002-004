"""实操证据：登记、提交与使用次数控制。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class PracticalEvidence:
    """一份实操证据：带内容指纹、有效期与使用次数上限。"""

    evidence_id: str
    holder_id: str
    fingerprint: str  # 内容指纹，防止同一证据换号重报
    category: str
    issued_on: date
    valid_from: date
    valid_to: date | None = None
    max_uses: int = 1

    def __post_init__(self) -> None:
        if not self.evidence_id or not self.holder_id or not self.fingerprint or not self.category:
            raise ValueError("证据信息不完整")
        if self.max_uses < 1:
            raise ValueError("使用次数上限至少为 1")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("证据有效期不合法")

    def usable_on(self, day: date) -> bool:
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)


@dataclass(frozen=True)
class EvidenceUse:
    """一次证据使用记录：绑定评审案件，同一案件重复提交不重复计次。"""

    evidence_id: str
    case_id: str
    used_on: date


class EvidenceLedger:
    """证据台账：保证同一证据不能借重复提交绕过有效期或使用次数。"""

    def __init__(self) -> None:
        self._by_id: dict[str, PracticalEvidence] = {}
        self._by_fingerprint: dict[tuple[str, str], str] = {}
        self._uses: list[EvidenceUse] = []

    def register(self, evidence: PracticalEvidence) -> None:
        if evidence.evidence_id in self._by_id:
            raise ValueError("证据编号已存在")
        key = (evidence.holder_id, evidence.fingerprint)
        if key in self._by_fingerprint:
            raise ValueError("同一证据不得换号重复登记")
        self._by_id[evidence.evidence_id] = evidence
        self._by_fingerprint[key] = evidence.evidence_id

    def get(self, evidence_id: str) -> PracticalEvidence:
        try:
            return self._by_id[evidence_id]
        except KeyError:
            raise KeyError(f"证据不存在: {evidence_id}") from None

    def submit(self, evidence_id: str, case_id: str, on: date) -> EvidenceUse:
        """提交证据用于某评审案件；超期、超次或换案件重复使用都会被拦截。"""
        evidence = self.get(evidence_id)
        if not evidence.usable_on(on):
            raise ValueError("证据不在有效期内，不能提交")
        for use in self._uses:
            if use.evidence_id == evidence_id and use.case_id == case_id:
                return use  # 同一案件重复提交：幂等，不重复计次
        used = [u for u in self._uses if u.evidence_id == evidence_id]
        if len(used) >= evidence.max_uses:
            raise ValueError("证据使用次数已达上限")
        use = EvidenceUse(evidence_id, case_id, on)
        self._uses.append(use)
        return use

    def uses_of(self, evidence_id: str) -> tuple[EvidenceUse, ...]:
        return tuple(u for u in self._uses if u.evidence_id == evidence_id)
