-- 当前可命中的正式标签缓存。唯一键不含 month，严格按稳定品类与 taxonomy 隔离。
CREATE TABLE IF NOT EXISTS tagging_label_consensus (
    id UUID PRIMARY KEY,
    category_key VARCHAR(32) NOT NULL,
    word TEXT NOT NULL,
    taxonomy_version INTEGER NOT NULL,
    label VARCHAR(16) NOT NULL,
    reason TEXT,
    decision_source VARCHAR(16) NOT NULL,
    representative_asin VARCHAR(16) NOT NULL,
    product_context_source VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT tagging_label_consensus_category_key_check
        CHECK (category_key IN ('pillow', 'stuffed_animals')),
    CONSTRAINT tagging_label_consensus_word_normalized_check
        CHECK (word = lower(btrim(word)) AND btrim(word) <> ''),
    CONSTRAINT tagging_label_consensus_taxonomy_version_check
        CHECK (taxonomy_version >= 1),
    CONSTRAINT tagging_label_consensus_label_check
        CHECK (label IN ('1核心词', '2外形', '3属性', '4痛点', '5规格', '6受众', '7场景', '8品牌', '无效词')),
    CONSTRAINT tagging_label_consensus_decision_source_check
        CHECK (decision_source IN ('AI_CONSENSUS', 'HUMAN_REVIEW')),
    CONSTRAINT tagging_label_consensus_identity_unique
        UNIQUE (category_key, word, taxonomy_version)
);

-- 每次形成正式标签的事件审计。CACHE_HIT 仅读取 current cache，不追加审计。
CREATE TABLE IF NOT EXISTS tagging_label_decisions (
    id UUID PRIMARY KEY,
    cache_id UUID NOT NULL REFERENCES tagging_label_consensus (id),
    category_key VARCHAR(32) NOT NULL,
    word TEXT NOT NULL,
    taxonomy_version INTEGER NOT NULL,
    label VARCHAR(16) NOT NULL,
    reason TEXT,
    decision_source VARCHAR(16) NOT NULL,
    representative_asin VARCHAR(16) NOT NULL,
    product_context_source VARCHAR(64) NOT NULL,
    provider_results JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT tagging_label_decisions_category_key_check
        CHECK (category_key IN ('pillow', 'stuffed_animals')),
    CONSTRAINT tagging_label_decisions_word_normalized_check
        CHECK (word = lower(btrim(word)) AND btrim(word) <> ''),
    CONSTRAINT tagging_label_decisions_taxonomy_version_check
        CHECK (taxonomy_version >= 1),
    CONSTRAINT tagging_label_decisions_label_check
        CHECK (label IN ('1核心词', '2外形', '3属性', '4痛点', '5规格', '6受众', '7场景', '8品牌', '无效词')),
    CONSTRAINT tagging_label_decisions_decision_source_check
        CHECK (decision_source IN ('AI_CONSENSUS', 'HUMAN_REVIEW'))
);

CREATE INDEX IF NOT EXISTS idx_tagging_label_decisions_identity_created
    ON tagging_label_decisions (
        category_key,
        word,
        taxonomy_version,
        created_at DESC,
        id DESC
    );
