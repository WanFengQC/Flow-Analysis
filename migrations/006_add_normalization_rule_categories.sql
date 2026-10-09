-- 旧版 004 的全局规则继续用 NULL 表示；新规则可绑定稳定品类 key。
ALTER TABLE normalization_active_rules
    ADD COLUMN IF NOT EXISTS category_key VARCHAR(64);

ALTER TABLE normalization_active_rules
    DROP CONSTRAINT IF EXISTS normalization_active_rules_category_key_check;

ALTER TABLE normalization_active_rules
    ADD CONSTRAINT normalization_active_rules_category_key_check
    CHECK (category_key IS NULL OR category_key IN ('pillow', 'stuffed_animals'));

CREATE INDEX IF NOT EXISTS idx_normalization_active_rules_category_current
    ON normalization_active_rules (category_key, rule_type, created_at DESC, id DESC)
    WHERE is_active;

-- 一次性 Hunter 迁移账本：保留原始确认、来源及暂缓原因，并用指纹保证幂等。
CREATE TABLE IF NOT EXISTS normalization_legacy_rule_imports (
    id UUID PRIMARY KEY,
    source_fingerprint VARCHAR(128) NOT NULL UNIQUE,
    source_system VARCHAR(64) NOT NULL,
    source_record_index INTEGER NOT NULL,
    category_key VARCHAR(64) NOT NULL,
    raw_phrase TEXT NOT NULL,
    normalized_phrase TEXT NOT NULL,
    legacy_status VARCHAR(64) NOT NULL,
    legacy_source VARCHAR(128),
    source_scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    import_status VARCHAR(32) NOT NULL,
    conflict_reason TEXT,
    active_rule_id UUID REFERENCES normalization_active_rules (id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT normalization_legacy_rule_imports_category_key_check
        CHECK (category_key IN ('pillow', 'stuffed_animals')),
    CONSTRAINT normalization_legacy_rule_imports_status_check
        CHECK (import_status IN (
            'IMPORTED', 'DEFERRED_CHAIN_CONFLICT',
            'EXCLUDED_REJECTED', 'EXCLUDED_PENDING'
        ))
);

CREATE INDEX IF NOT EXISTS idx_normalization_legacy_rule_imports_category_status
    ON normalization_legacy_rule_imports (category_key, import_status, source_record_index);
