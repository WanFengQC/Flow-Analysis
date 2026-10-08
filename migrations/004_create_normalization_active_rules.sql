-- 归一化审核历史与当前生效规则严格分离：本表只保存已经完整校验并显式应用的规则。
CREATE TABLE IF NOT EXISTS normalization_active_rules (
    id UUID PRIMARY KEY,
    rule_type VARCHAR(16) NOT NULL,
    variants JSONB NOT NULL,
    canonical TEXT NOT NULL,
    source_candidate_id TEXT,
    source_reason_types JSONB NOT NULL DEFAULT '[]'::jsonb,
    supersedes_rule_id UUID REFERENCES normalization_active_rules (id),
    revision INTEGER NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT normalization_active_rules_rule_type_check
        CHECK (rule_type IN ('WORD', 'PHRASE')),
    CONSTRAINT normalization_active_rules_variants_array_check
        CHECK (jsonb_typeof(variants) = 'array' AND jsonb_array_length(variants) > 0),
    CONSTRAINT normalization_active_rules_canonical_check
        CHECK (btrim(canonical) <> ''),
    CONSTRAINT normalization_active_rules_revision_check
        CHECK (revision >= 1)
);

-- 规则管理操作必须 append-only；撤销、替换都不能抹掉曾经的有效版本。
CREATE TABLE IF NOT EXISTS normalization_active_rule_audits (
    id UUID PRIMARY KEY,
    rule_id UUID NOT NULL REFERENCES normalization_active_rules (id),
    action VARCHAR(16) NOT NULL,
    rule_snapshot JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT normalization_active_rule_audits_action_check
        CHECK (action IN ('CREATE', 'UPDATE', 'REVOKE', 'APPLY'))
);

CREATE INDEX IF NOT EXISTS idx_normalization_active_rules_current
    ON normalization_active_rules (rule_type, created_at DESC, id DESC)
    WHERE is_active;

CREATE INDEX IF NOT EXISTS idx_normalization_active_rule_audits_rule_created
    ON normalization_active_rule_audits (rule_id, created_at DESC, id DESC);
