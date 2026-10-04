"""领域错误类型。"""


class RecognitionDomainError(Exception):
    """互认领域错误的基类。"""


class ValidationError(RecognitionDomainError, ValueError):
    """对象或参数不满足领域约束（同时是 ValueError，兼容基础校验口径）。"""


class CaseStateError(RecognitionDomainError):
    """评审案件当前状态不允许该操作。"""


class ReviewerConflictError(RecognitionDomainError):
    """评审人存在利益冲突，应当回避。"""


class ConcurrentReviewError(RecognitionDomainError):
    """并发评审：案件版本号已过期或结论已被他人先行生效。"""


class EffectiveConclusionConflict(ConcurrentReviewError):
    """已有生效结论，平行评审不得覆盖；如需变更必须走复议/替代流程。"""


class EvidenceExpiredError(RecognitionDomainError):
    """证据在使用日期已过有效期。"""


class EvidenceUsageExhaustedError(RecognitionDomainError):
    """证据使用次数已耗尽，重复提交不能增加次数。"""


class StandardLineageError(RecognitionDomainError):
    """标准版本接续关系不合法。"""
