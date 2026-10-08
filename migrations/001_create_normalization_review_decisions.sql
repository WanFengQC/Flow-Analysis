-- 人工归一审核历史。该表只记录用户明确执行的审核动作，绝不代表自动生效规则。
CREATE TABLE IF NOT EXISTS normalization_review_decisions (
    id UUID PRIMARY KEY,
    candidate_fingerprint VARCHAR(64) NOT NULL,
    candidate_id VARCHAR(128) NOT NULL,
    decision VARCHAR(16) NOT NULL,
    rule_type VARCHAR(16) NOT NULL,
    variants JSONB NOT NULL,
    suggested_canonical TEXT,
    approved_canonical TEXT,
    reason_types JSONB NOT NULL,
    context_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    normalization_revision INTEGER NOT NULL CHECK (normalization_revision >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT normalization_review_decisions_decision_check
        CHECK (decision IN ('APPROVED', 'REJECTED', 'SKIPPED')),
    CONSTRAINT normalization_review_decisions_rule_type_check
        CHECK (rule_type IN ('WORD', 'PHRASE')),
    CONSTRAINT normalization_review_decisions_approved_canonical_check
        CHECK (
            (decision = 'APPROVED' AND approved_canonical IS NOT NULL
             AND btrim(approved_canonical) <> '')
            OR
            (decision IN ('REJECTED', 'SKIPPED') AND approved_canonical IS NULL)
        )
);

-- 同一候选允许有多条历史决定，故 candidate_fingerprint 不能唯一。
CREATE INDEX IF NOT EXISTS idx_normalization_review_decisions_fingerprint_created
    ON normalization_review_decisions (
        candidate_fingerprint,
        created_at DESC,
        id DESC
    );

CREATE INDEX IF NOT EXISTS idx_normalization_review_decisions_created_at
    ON normalization_review_decisions (created_at DESC);
