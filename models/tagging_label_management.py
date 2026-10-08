"""标签管理页使用的审计模型，和 AI/审核正式决定事件保持分层。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class TaggingLabelManagementAction(StrEnum):
    """管理页对当前标签缓存执行的逻辑操作。"""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


@dataclass(frozen=True, slots=True)
class TaggingLabelManagementAuditRecord:
    """逻辑删除前后快照和人工改写历史，绝不覆盖旧标签审计。"""

    id: UUID
    cache_id: UUID
    action: TaggingLabelManagementAction
    before_snapshot: dict[str, Any] | None
    after_snapshot: dict[str, Any] | None
    created_at: datetime | None = None
