"""互认结论台账与人事时点查询。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .catalog import PositionProfile
from .contracts import StandardVersion
from .review import Decision, ReviewBoard


@dataclass(frozen=True)
class Recognition:
    """一条不可改写的互认结论快照：历史招聘依据永远保留。"""

    recognition_id: str
    applicant_id: str
    case_id: str
    mapping_id: str
    decision: Decision
    units: tuple[str, ...]
    standard: StandardVersion  # 作出结论时所依据的标准版本
    decided_on: date
    valid_from: date
    valid_to: date | None = None
    supersedes: str | None = None  # 被本结论取代的旧结论（复议或标准升级平移）
    conditions: tuple[str, ...] = ()  # 部分等同/附加考核时的附加条件

    def __post_init__(self) -> None:
        if not self.recognition_id or not self.applicant_id or not self.units:
            raise ValueError("结论信息不完整")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("结论有效期不合法")

    def effective_on(self, day: date) -> bool:
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)


@dataclass(frozen=True)
class RecognitionLapse:
    """结论失效记录：只追加，不改写原结论。"""

    recognition_id: str
    lapsed_on: date
    reason: str


class RecognitionBook:
    """只增不改的结论台账。"""

    def __init__(self) -> None:
        self._records: list[Recognition] = []
        self._lapses: list[RecognitionLapse] = []

    def record(self, recognition: Recognition) -> None:
        if any(r.recognition_id == recognition.recognition_id for r in self._records):
            raise ValueError("结论编号已存在")
        if recognition.supersedes is not None and not any(
            r.recognition_id == recognition.supersedes for r in self._records
        ):
            raise ValueError("被取代的结论不存在")
        self._records.append(recognition)

    def lapse(self, recognition_id: str, on: date, reason: str) -> None:
        if not any(r.recognition_id == recognition_id for r in self._records):
            raise KeyError(f"结论不存在: {recognition_id}")
        self._lapses.append(RecognitionLapse(recognition_id, on, reason))

    def find_by_case(self, case_id: str) -> Recognition | None:
        for record in reversed(self._records):
            if record.case_id == case_id:
                return record
        return None

    def all_records(self) -> tuple[Recognition, ...]:
        return tuple(self._records)

    def latest_records(self, on: date) -> tuple[Recognition, ...]:
        """每条结论链在某时点最新且仍有效的记录（复议/升级平移只影响该时点之后）。"""
        by_id = {r.recognition_id: r for r in self._records}

        def root_of(recognition: Recognition) -> str:
            seen = recognition
            while seen.supersedes is not None and seen.supersedes in by_id:
                seen = by_id[seen.supersedes]
            return seen.recognition_id

        latest: dict[str, Recognition] = {}
        for record in self._records:
            if record.decided_on > on:
                continue
            root = root_of(record)
            if root not in latest or record.decided_on > latest[root].decided_on:
                latest[root] = record

        lapsed_on = {lapse.recognition_id: lapse.lapsed_on for lapse in self._lapses}
        result = []
        for record in latest.values():
            lapsed = lapsed_on.get(record.recognition_id)
            if lapsed is not None and lapsed <= on:
                continue
            if not record.effective_on(on):
                continue
            result.append(record)
        return tuple(result)

    def effective_for(self, applicant_id: str, on: date) -> tuple[Recognition, ...]:
        return tuple(r for r in self.latest_records(on) if r.applicant_id == applicant_id)


@dataclass(frozen=True)
class PositionMatch:
    """岗位匹配结果：已认可与仍缺失的能力单元。"""

    position_id: str
    title: str
    recognized_units: tuple[str, ...]
    missing_units: tuple[str, ...]


@dataclass(frozen=True)
class ApplicantReport:
    """人事时点查询结果。"""

    applicant_id: str
    as_of: date
    recognizable_positions: tuple[PositionMatch, ...]  # 当时可认可的岗位
    partial_positions: tuple[PositionMatch, ...]  # 部分认可、仍有缺失能力的岗位
    pending_reconsiderations: tuple[str, ...]  # 待复议事项（案件编号）
    standard_versions: tuple[StandardVersion, ...]  # 本次结论所依据的标准版本


def assess_applicant(
    applicant_id: str,
    as_of: date,
    positions: tuple[PositionProfile, ...] | list[PositionProfile],
    book: RecognitionBook,
    board: ReviewBoard,
) -> ApplicantReport:
    """重建某历史时点的认可状态：可认可岗位、缺失能力、待复议事项与所依据标准版本。"""
    recognitions = book.effective_for(applicant_id, as_of)
    units_by_standard: dict[tuple[str, int], set[str]] = {}
    for record in recognitions:
        units_by_standard.setdefault(record.standard.key(), set()).update(record.units)

    recognizable: list[PositionMatch] = []
    partial: list[PositionMatch] = []
    standards_used: dict[tuple[str, int], StandardVersion] = {
        record.standard.key(): record.standard for record in recognitions
    }
    for position in positions:
        have = units_by_standard.get(position.standard.key(), set())
        recognized = tuple(u for u in position.required_units if u in have)
        missing = tuple(u for u in position.required_units if u not in have)
        if not missing:
            recognizable.append(PositionMatch(position.position_id, position.title, recognized, missing))
            standards_used[position.standard.key()] = position.standard
        elif recognized:
            partial.append(PositionMatch(position.position_id, position.title, recognized, missing))
            standards_used[position.standard.key()] = position.standard

    pending = tuple(sorted(c.case_id for c in board.pending_reconsiderations(applicant_id, as_of)))
    versions = tuple(sorted(standards_used.values(), key=lambda s: (s.entity_id, s.revision)))
    return ApplicantReport(
        applicant_id=applicant_id,
        as_of=as_of,
        recognizable_positions=tuple(recognizable),
        partial_positions=tuple(partial),
        pending_reconsiderations=pending,
        standard_versions=versions,
    )
