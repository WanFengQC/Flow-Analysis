-- Hunter 历史共识缓存没有逐条区分 AI 共识与人工确认；新增明确的迁移来源，
-- 避免把导入数据伪造为本项目当前运行时产生的正式决定。
ALTER TABLE tagging_label_consensus
    ALTER COLUMN decision_source TYPE VARCHAR(32);

ALTER TABLE tagging_label_consensus
    DROP CONSTRAINT IF EXISTS tagging_label_consensus_decision_source_check;

ALTER TABLE tagging_label_consensus
    ADD CONSTRAINT tagging_label_consensus_decision_source_check
    CHECK (
        decision_source IN (
            'AI_CONSENSUS',
            'HUMAN_REVIEW',
            'HISTORICAL_IMPORT'
        )
    );

ALTER TABLE tagging_label_decisions
    ALTER COLUMN decision_source TYPE VARCHAR(32);

ALTER TABLE tagging_label_decisions
    DROP CONSTRAINT IF EXISTS tagging_label_decisions_decision_source_check;

ALTER TABLE tagging_label_decisions
    ADD CONSTRAINT tagging_label_decisions_decision_source_check
    CHECK (
        decision_source IN (
            'AI_CONSENSUS',
            'HUMAN_REVIEW',
            'HISTORICAL_IMPORT'
        )
    );
