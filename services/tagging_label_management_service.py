"""标签管理页的人工 CRUD 服务，保持 AI Pipeline 与缓存读取分层。"""

from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import UUID, uuid4

from psycopg import AsyncConnection

from models.tagging import TagLabel
from models.tagging_label import (
    TAGGING_TAXONOMY_VERSION,
    TaggingCategoryKey,
    TaggingDecisionSource,
    TaggingLabelCacheRecord,
    TaggingLabelDecisionRecord,
    normalize_cache_word,
    require_category_key,
)
from models.tagging_label_management import (
    TaggingLabelManagementAction,
    TaggingLabelManagementAuditRecord,
)
from repositories.database import DatabaseManager
from repositories.tagging_label_management_repository import (
    TaggingLabelManagementRepository,
)


class TaggingLabelVersionConflictError(RuntimeError):
    """当前标签已由另一个管理操作改变，需要刷新后重试。"""


class TaggingLabelIdentityExistsError(ValueError):
    """用户尝试新增已经生效的同品类、词和 taxonomy 标签。"""


class TaggingLabelManagementService:
    """持久化管理标签；所有写入都以 MANUAL_MANAGEMENT 明确标识。"""

    def __init__(self, database: DatabaseManager) -> None:
        self._database = database

    async def list_current(
        self,
        *,
        category_key: str | None,
        label: str | None,
        decision_source: str | None,
        search: str | None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[TaggingLabelCacheRecord], int]:
        """管理列表严格查询 current active cache，不包含逻辑删除记录。"""

        return await TaggingLabelManagementRepository(
            self._database
        ).list_current(
            category_key=(
                require_category_key(category_key)
                if category_key
                else None
            ),
            label=TagLabel(label) if label else None,
            decision_source=(
                TaggingDecisionSource(decision_source)
                if decision_source
                else None
            ),
            search=search,
            limit=limit,
            offset=offset,
        )

    async def list_history(self, cache_id: UUID) -> list[dict[str, object]]:
        """返回旧标签形成审计和管理审计的合并只读历史。"""

        return await TaggingLabelManagementRepository(
            self._database
        ).list_history(cache_id)

    async def create_manual_label(
        self,
        *,
        category_key: str,
        word: str,
        label: str,
        reason: str,
        taxonomy_version: int = TAGGING_TAXONOMY_VERSION,
    ) -> TaggingLabelCacheRecord:
        """人工新增当前标签；已删除 identity 只允许明确人工重新激活。"""

        category = require_category_key(category_key)
        normalized_word = normalize_cache_word(word)
        parsed_label = TagLabel(label)
        clean_reason = self._require_reason(reason)
        self._require_taxonomy_version(taxonomy_version)
        async with self._transaction_scope() as connection:
            repository = TaggingLabelManagementRepository(self._database, connection)
            existing = await repository.get_by_identity(
                category_key=category,
                word=normalized_word,
                taxonomy_version=taxonomy_version,
                for_update=True,
            )
            if existing is not None and existing.is_active:
                raise TaggingLabelIdentityExistsError("当前标签已存在，请改用编辑")
            if existing is None:
                current = await repository.insert_manual(
                    TaggingLabelCacheRecord(
                        id=uuid4(),
                        category_key=category,
                        word=normalized_word,
                        taxonomy_version=taxonomy_version,
                        label=parsed_label,
                        reason=clean_reason,
                        decision_source=TaggingDecisionSource.MANUAL_MANAGEMENT,
                        representative_asin=None,
                        product_context_source=None,
                    )
                )
                before_snapshot = None
            else:
                before_snapshot = self._snapshot(existing)
                current = await repository.reactivate_manual(
                    previous=existing,
                    label=parsed_label,
                    reason=clean_reason,
                )
                if current is None:
                    raise TaggingLabelVersionConflictError("标签已被更新，请刷新后重试")
            await self._append_label_decision(repository, current)
            await self._append_management_audit(
                repository,
                current,
                TaggingLabelManagementAction.CREATE,
                before_snapshot=before_snapshot,
            )
            return current

    async def update_manual_label(
        self,
        *,
        cache_id: UUID,
        expected_revision: int,
        category_key: str,
        word: str,
        label: str,
        reason: str,
    ) -> TaggingLabelCacheRecord:
        """编辑人工标签；identity 改动以新缓存身份事务迁移而非原地覆写。"""

        target_category = require_category_key(category_key)
        target_word = normalize_cache_word(word)
        parsed_label = TagLabel(label)
        clean_reason = self._require_reason(reason)
        async with self._transaction_scope() as connection:
            repository = TaggingLabelManagementRepository(self._database, connection)
            before = await repository.get_current_by_id(cache_id, for_update=True)
            if before is None or before.revision != expected_revision:
                raise TaggingLabelVersionConflictError("标签已被更新，请刷新后重试")
            if (
                before.category_key == target_category
                and before.word == target_word
            ):
                current = await repository.update_manual(
                    cache_id=cache_id,
                    revision=expected_revision,
                    label=parsed_label,
                    reason=clean_reason,
                )
                if current is None:
                    raise TaggingLabelVersionConflictError("标签已被更新，请刷新后重试")
                await self._append_label_decision(repository, current)
                await self._append_management_audit(
                    repository,
                    current,
                    TaggingLabelManagementAction.UPDATE,
                    before_snapshot=self._snapshot(before),
                )
                return current

            target_before = await repository.get_by_identity(
                category_key=target_category,
                word=target_word,
                taxonomy_version=before.taxonomy_version,
                for_update=True,
            )
            if target_before is not None and target_before.is_active:
                raise TaggingLabelIdentityExistsError(
                    "目标品类和标准词已有有效标签，不能覆盖另一条标签"
                )
            if target_before is None:
                target = await repository.insert_manual(
                    TaggingLabelCacheRecord(
                        id=uuid4(),
                        category_key=target_category,
                        word=target_word,
                        taxonomy_version=before.taxonomy_version,
                        label=parsed_label,
                        reason=clean_reason,
                        decision_source=TaggingDecisionSource.MANUAL_MANAGEMENT,
                        representative_asin=None,
                        product_context_source=None,
                    )
                )
                target_action = TaggingLabelManagementAction.CREATE
            else:
                target = await repository.reactivate_manual(
                    previous=target_before,
                    label=parsed_label,
                    reason=clean_reason,
                )
                if target is None:
                    raise TaggingLabelVersionConflictError("目标标签已被更新，请刷新后重试")
                target_action = TaggingLabelManagementAction.UPDATE
            retired = await repository.deactivate_current(
                cache_id=before.id,
                revision=expected_revision,
            )
            if retired is None:
                raise TaggingLabelVersionConflictError("标签已被更新，请刷新后重试")
            await self._append_label_decision(repository, target)
            await self._append_identity_migration_audits(
                repository,
                previous=before,
                retired=retired,
                target_before=target_before,
                target=target,
                target_action=target_action,
            )
            return target

    async def delete_manual_label(
        self,
        *,
        cache_id: UUID,
        expected_revision: int,
    ) -> TaggingLabelCacheRecord:
        """逻辑删除当前缓存，保留 cache id 与所有旧审计。"""

        async with self._transaction_scope() as connection:
            repository = TaggingLabelManagementRepository(self._database, connection)
            before = await repository.get_current_by_id(cache_id, for_update=True)
            if before is None or before.revision != expected_revision:
                raise TaggingLabelVersionConflictError("标签已被更新，请刷新后重试")
            current = await repository.deactivate_current(
                cache_id=cache_id,
                revision=expected_revision,
            )
            if current is None:
                raise TaggingLabelVersionConflictError("标签已被更新，请刷新后重试")
            await self._append_management_audit(
                repository,
                current,
                TaggingLabelManagementAction.DELETE,
                before_snapshot=self._snapshot(before),
            )
            return current

    async def _append_label_decision(
        self,
        repository: TaggingLabelManagementRepository,
        record: TaggingLabelCacheRecord,
    ) -> None:
        """人工管理形成的标签追加到既有标签审计，不伪造 Provider 结果。"""

        await repository.append_decision(
            TaggingLabelDecisionRecord(
                id=uuid4(),
                cache_id=record.id,
                category_key=record.category_key,
                word=record.word,
                taxonomy_version=record.taxonomy_version,
                label=record.label,
                reason=record.reason,
                decision_source=TaggingDecisionSource.MANUAL_MANAGEMENT,
                representative_asin=None,
                product_context_source=None,
                provider_results={},
            )
        )

    async def _append_management_audit(
        self,
        repository: TaggingLabelManagementRepository,
        record: TaggingLabelCacheRecord,
        action: TaggingLabelManagementAction,
        *,
        before_snapshot: dict[str, object] | None,
    ) -> None:
        await repository.append_management_audit(
            TaggingLabelManagementAuditRecord(
                id=uuid4(),
                cache_id=record.id,
                action=action,
                before_snapshot=before_snapshot,
                after_snapshot=self._snapshot(record),
            )
        )

    async def _append_identity_migration_audits(
        self,
        repository: TaggingLabelManagementRepository,
        *,
        previous: TaggingLabelCacheRecord,
        retired: TaggingLabelCacheRecord,
        target_before: TaggingLabelCacheRecord | None,
        target: TaggingLabelCacheRecord,
        target_action: TaggingLabelManagementAction,
    ) -> None:
        """记录双向 identity 链接，使新旧缓存均能追溯人工迁移。"""

        retired_snapshot = self._snapshot(retired)
        retired_snapshot["identityChange"] = {
            "migratedToCacheId": str(target.id),
        }
        target_snapshot = self._snapshot(target)
        target_snapshot["identityChange"] = {
            "migratedFromCacheId": str(previous.id),
        }
        await repository.append_management_audit(
            TaggingLabelManagementAuditRecord(
                id=uuid4(),
                cache_id=retired.id,
                action=TaggingLabelManagementAction.UPDATE,
                before_snapshot=self._snapshot(previous),
                after_snapshot=retired_snapshot,
            )
        )
        await repository.append_management_audit(
            TaggingLabelManagementAuditRecord(
                id=uuid4(),
                cache_id=target.id,
                action=target_action,
                before_snapshot=(
                    self._snapshot(target_before) if target_before is not None else None
                ),
                after_snapshot=target_snapshot,
            )
        )

    @asynccontextmanager
    async def _transaction_scope(self) -> AsyncIterator[AsyncConnection]:
        async with self._database.transaction() as connection:
            yield connection

    @staticmethod
    def _require_reason(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("人工原因不能为空")
        return value.strip()

    @staticmethod
    def _require_taxonomy_version(value: int) -> None:
        if not isinstance(value, int) or value < 1:
            raise ValueError("taxonomy_version 必须大于等于 1")

    @staticmethod
    def _snapshot(record: TaggingLabelCacheRecord) -> dict[str, object]:
        return {
            "id": str(record.id),
            "categoryKey": record.category_key.value,
            "word": record.word,
            "taxonomyVersion": record.taxonomy_version,
            "label": record.label.value,
            "reason": record.reason,
            "decisionSource": record.decision_source.value,
            "representativeAsin": record.representative_asin,
            "productContextSource": record.product_context_source,
            "revision": record.revision,
            "isActive": record.is_active,
        }
