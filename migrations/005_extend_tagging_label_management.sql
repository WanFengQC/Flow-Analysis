-- 管理页可创建不关联具体 ASIN 的人工标签；AI 写入仍由 Service 继续校验其背景字段。
ALTER TABLE tagging_label_consensus
    ALTER COLUMN representative_asin DROP NOT NULL;

ALTER TABLE tagging_label_consensus
    ALTER COLUMN product_context_source DROP NOT NULL;

ALTER TABLE tagging_label_consensus
    ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 1;

ALTER TABLE tagging_label_consensus
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE tagging_label_consensus
    ADD COLUMN IF NOT EXISTS invalidated_at TIMESTAMPTZ;

ALTER TABLE tagging_label_consensus
    DROP CONSTRAINT IF EXISTS tagging_label_consensus_decision_source_check;

ALTER TABLE tagging_label_consensus
    ADD CONSTRAINT tagging_label_consensus_decision_source_check
    CHECK (
        decision_source IN (
            'AI_CONSENSUS',
            'HUMAN_REVIEW',
            'HISTORICAL_IMPORT',
            'MANUAL_MANAGEMENT'
        )
    );

ALTER TABLE tagging_label_decisions
    ALTER COLUMN representative_asin DROP NOT NULL;

ALTER TABLE tagging_label_decisions
    ALTER COLUMN product_context_source DROP NOT NULL;

ALTER TABLE tagging_label_decisions
    DROP CONSTRAINT IF EXISTS tagging_label_decisions_decision_source_check;

ALTER TABLE tagging_label_decisions
    ADD CONSTRAINT tagging_label_decisions_decision_source_check
    CHECK (
        decision_source IN (
            'AI_CONSENSUS',
            'HUMAN_REVIEW',
            'HISTORICAL_IMPORT',
            'MANUAL_MANAGEMENT'
        )
    );

-- 管理动作与 AI/审核形成标签的原始审计分开保存，避免篡改旧审计含义。
CREATE TABLE IF NOT EXISTS tagging_label_management_audits (
    id UUID PRIMARY KEY,
    cache_id UUID NOT NULL REFERENCES tagging_label_consensus (id),
    action VARCHAR(16) NOT NULL,
    before_snapshot JSONB,
    after_snapshot JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT tagging_label_management_audits_action_check
        CHECK (action IN ('CREATE', 'UPDATE', 'DELETE'))
);

CREATE INDEX IF NOT EXISTS idx_tagging_label_consensus_current
    ON tagging_label_consensus (
        category_key,
        taxonomy_version,
        word,
        updated_at DESC,
        id DESC
    )
    WHERE is_active;

CREATE INDEX IF NOT EXISTS idx_tagging_label_management_audits_cache_created
    ON tagging_label_management_audits (cache_id, created_at DESC, id DESC);
