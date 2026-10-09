-- Flow Analysis PostgreSQL 角色隔离方案（仅供 DBA 在批准后的维护窗口审阅/执行）。
-- 本文件不包含密码，不应由桌面客户端、migration CLI 或安装包自动执行。
-- 必须先完成并验收 001~005；随后才可执行下方已有表授权与所有权调整。
-- 前提：flow_analysis 是 Flow Analysis 专用数据库；若与其它系统共享，必须先
-- 逐表确认对象归属，不能直接套用所有权调整步骤。

-- 1. 运行期账号：只用于 EXE，禁止 DDL、建库、建角色、复制及超级权限。
CREATE ROLE flow_analysis_app
    LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- 2. migration 账号：只在受控维护窗口使用，不进入 EXE 或仓库。
CREATE ROLE flow_analysis_migrator
    LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- 3. 数据库管理员账号不属于本授权脚本，不允许配置到客户端。

-- 仅允许两个专用账号连接业务库；按 DBA 策略决定是否先撤销 PUBLIC CONNECT。
GRANT CONNECT ON DATABASE flow_analysis TO flow_analysis_app, flow_analysis_migrator;
GRANT USAGE ON SCHEMA public TO flow_analysis_app, flow_analysis_migrator;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM flow_analysis_app;

-- 运行账号仅能读写当前业务表，不具有 TRUNCATE、REFERENCES、TRIGGER 或 DDL 权限。
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
    normalization_review_decisions,
    normalization_active_rules,
    normalization_active_rule_audits,
    tagging_label_consensus,
    tagging_label_decisions,
    tagging_label_management_audits
TO flow_analysis_app;

GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public
TO flow_analysis_app;

-- migration 账号必须是受控业务表的 owner，才能执行 001~005 以及后续 ALTER。
-- 仅对已核验属于 Flow Analysis 的表运行以下所有权变更。
ALTER TABLE normalization_review_decisions OWNER TO flow_analysis_migrator;
ALTER TABLE normalization_active_rules OWNER TO flow_analysis_migrator;
ALTER TABLE normalization_active_rule_audits OWNER TO flow_analysis_migrator;
ALTER TABLE tagging_label_consensus OWNER TO flow_analysis_migrator;
ALTER TABLE tagging_label_decisions OWNER TO flow_analysis_migrator;
ALTER TABLE tagging_label_management_audits OWNER TO flow_analysis_migrator;

-- 使 migration 新建对象自动授予应用账号最小业务权限。
ALTER DEFAULT PRIVILEGES FOR ROLE flow_analysis_migrator IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO flow_analysis_app;
ALTER DEFAULT PRIVILEGES FOR ROLE flow_analysis_migrator IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO flow_analysis_app;

-- 审计后由 DBA 单独设置两个 LOGIN 账号的密码/认证方式；密码不得写入本文件。
-- 运行客户端只配置 flow_analysis_app；run_migrations.py 只在维护窗口配置
-- flow_analysis_migrator，并要求 --database/--confirm-database 双重确认。
