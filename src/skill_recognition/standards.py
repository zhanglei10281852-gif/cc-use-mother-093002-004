"""标准（国家/机构）、工种版本与能力单元。

标准按"标准族 entity_id + revision"版本化；标准升级通过前驱指针接续，
旧版本的内容（发布日期、能力单元）永不改变，只追加废止事实，
因此任何历史时点的招聘依据都可以被原样复原。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, NamedTuple

from .errors import StandardLineageError, ValidationError


@dataclass(frozen=True)
class CompetencyUnit:
    """能力单元：标准内不可再分的实操能力要求。"""

    unit_code: str
    title: str

    def __post_init__(self) -> None:
        if not self.unit_code or not self.title:
            raise ValidationError("能力单元编码与名称不能为空")


@dataclass(frozen=True)
class StandardVersion:
    """标准/工种的某个版本（如《车工国家职业标准》2018 版）。

    issue_date 为该版本生效日期。retire_date 仅在离线构造完整历史数据时
    使用；正常流程由登记簿在后继版本发布时追加废止事实。
    predecessor_revision 指向上一修订号，构成标准谱系。
    """

    entity_id: str
    display_name: str
    revision: int
    issue_date: str
    units: frozenset[str] = field(default_factory=frozenset)
    retire_date: str | None = None
    predecessor_revision: int | None = None
    origin: str = "国家"

    def __post_init__(self) -> None:
        if not self.entity_id or not self.display_name or self.revision < 1:
            raise ValidationError("版本化实体信息不合法")
        if not self.issue_date:
            raise ValidationError("标准必须有发布日期")
        if any(not u for u in self.units):
            raise ValidationError("能力单元编码不能为空")
        if self.retire_date is not None and self.retire_date < self.issue_date:
            raise ValidationError("废止日期不能早于发布日期")
        if self.predecessor_revision is not None:
            if self.predecessor_revision < 1 or self.predecessor_revision >= self.revision:
                raise StandardLineageError("前驱修订号必须小于当前修订号")

    @property
    def key(self) -> tuple[str, int]:
        return (self.entity_id, self.revision)

    def is_effective_on(self, day: str) -> bool:
        """该版本在指定日期是否有效（含发布当日，不含废止当日）。"""
        if day < self.issue_date:
            return False
        return self.retire_date is None or day < self.retire_date

    def contains(self, unit_code: str) -> bool:
        return unit_code in self.units


class Retirement(NamedTuple):
    retire_date: str
    successor_key: tuple[str, int]


class StandardRegistry:
    """标准版本登记簿：保存全部历史版本与废止事实，提供谱系查询。"""

    def __init__(self) -> None:
        self._versions: dict[tuple[str, int], StandardVersion] = {}
        self._retirements: dict[tuple[str, int], Retirement] = {}

    def publish(self, version: StandardVersion) -> StandardVersion:
        if version.key in self._versions:
            raise ValidationError(f"标准版本已存在且不可改写: {version.key}")
        if version.predecessor_revision is not None:
            pred_key = (version.entity_id, version.predecessor_revision)
            if pred_key not in self._versions:
                raise StandardLineageError(f"前驱版本不存在: {pred_key}")
            if pred_key in self._retirements:
                raise StandardLineageError("前驱版本已有后继，不能分叉")
        self._versions[version.key] = version
        return version

    def publish_successor(
        self, version: StandardVersion, retire_date: str
    ) -> StandardVersion:
        """发布升级版本：登记新版本，并给前驱版本追加废止事实（不改写其内容）。"""
        if version.predecessor_revision is None:
            raise StandardLineageError("升级版本必须声明前驱修订号")
        pred_key = (version.entity_id, version.predecessor_revision)
        if pred_key not in self._versions:
            raise StandardLineageError(f"前驱版本不存在: {pred_key}")
        if pred_key in self._retirements:
            raise StandardLineageError("前驱版本已有后继，不能重复升级")
        if retire_date < version.issue_date:
            raise ValidationError("前驱废止日期不能早于新版本发布日期")
        self.publish(version)
        self._retirements[pred_key] = Retirement(retire_date, version.key)
        return version

    def get(self, entity_id: str, revision: int) -> StandardVersion:
        try:
            return self._versions[(entity_id, revision)]
        except KeyError:
            raise ValidationError(f"标准版本不存在: {(entity_id, revision)}") from None

    def retirement_of(self, key: tuple[str, int]) -> Retirement | None:
        return self._retirements.get(key)

    def versions_of(self, entity_id: str) -> list[StandardVersion]:
        found = [v for v in self._versions.values() if v.entity_id == entity_id]
        return sorted(found, key=lambda v: v.revision)

    def is_effective_on(self, key: tuple[str, int], day: str) -> bool:
        version = self._versions[key]
        if day < version.issue_date:
            return False
        retirement = self._retirements.get(key)
        if retirement is not None:
            return day < retirement.retire_date
        return version.retire_date is None or day < version.retire_date

    def effective_version_on(self, entity_id: str, day: str) -> StandardVersion | None:
        candidates = [
            v
            for v in self.versions_of(entity_id)
            if self.is_effective_on(v.key, day)
        ]
        if len(candidates) > 1:
            raise StandardLineageError(f"{entity_id} 在 {day} 存在多个有效版本")
        return candidates[0] if candidates else None

    def lineage(self, entity_id: str) -> list[StandardVersion]:
        """按修订号与前驱指针返回谱系链（最早 → 最新），校验无断裂、无分叉。"""
        versions = self.versions_of(entity_id)
        if not versions:
            return []
        by_rev = {v.revision: v for v in versions}
        chain: list[StandardVersion] = []
        heads = [v for v in versions if v.revision not in {x.predecessor_revision for x in versions}]
        if len(heads) != 1:
            raise StandardLineageError(f"标准谱系必须有且仅有一个最新版本: {entity_id}")
        cur: StandardVersion | None = heads[0]
        while cur is not None:
            chain.append(cur)
            pred = cur.predecessor_revision
            cur = by_rev[pred] if pred is not None else None
        chain.reverse()
        if [v.revision for v in chain] != sorted(by_rev):
            raise StandardLineageError(f"标准谱系断裂: {entity_id}")
        return chain

    def successor_map(
        self, entity_id: str
    ) -> dict[tuple[str, int], tuple[str, int]]:
        """旧版本 -> 后继版本。"""
        result: dict[tuple[str, int], tuple[str, int]] = {}
        for v in self.versions_of(entity_id):
            if v.predecessor_revision is not None:
                result[(entity_id, v.predecessor_revision)] = v.key
        return result

    def all_versions(self) -> Iterable[StandardVersion]:
        return list(self._versions.values())
