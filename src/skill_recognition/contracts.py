"""基础契约再出口。

类型定义已按领域拆分，保留本模块仅为兼容最早的导入路径：

* :class:`StandardVersion` 见 :mod:`skill_recognition.standards`
* :class:`EvidenceRecord` 见 :mod:`skill_recognition.evidence`
"""
from .evidence import EvidenceRecord
from .standards import StandardVersion

__all__ = ["StandardVersion", "EvidenceRecord"]
