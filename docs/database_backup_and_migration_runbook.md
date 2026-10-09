# Flow Analysis PostgreSQL 备份、恢复与受控迁移方案

本文是 3.0.9 前的发布执行方案，不授权任何人现在对正式库执行写入。

## 备份选择

Flow Analysis 是单一业务数据库，发布前首选 `pg_dump` 自定义格式逻辑备份：它可
单库、可校验、可恢复到隔离数据库，最适合验证 004/005 这种业务 Schema 升级。

`pg_basebackup` 是整个 PostgreSQL 集群的物理备份，适用于已部署 WAL 归档、复制槽、
PITR 和整机灾备的运维体系；它不是本次应用 Schema 发布的替代品，也不应仅为本次
升级临时启用复制权限。

## 批准后的维护窗口步骤

1. 停止旧版 Flow Analysis 客户端并阻止新连接，记录维护开始时间。
2. 使用 DBA 账号和受保护的 `PGPASSFILE` 做备份；命令行不得出现密码：

   ```powershell
   pg_dump --format=custom --no-owner --no-privileges --file flow_analysis_pre_3_0_9.dump flow_analysis
   Get-FileHash flow_analysis_pre_3_0_9.dump -Algorithm SHA256
   pg_dumpall --globals-only --no-role-passwords --file flow_analysis_roles_pre_3_0_9.sql
   ```

3. 把备份和 SHA-256 放到受控、加密、非发布目录；不要放入 Git、安装包或更新目录。
4. 在隔离 PostgreSQL 实例创建临时数据库并恢复，不允许使用正式库作恢复验证：

   ```powershell
   createdb flow_analysis_restore_check
   pg_restore --dbname flow_analysis_restore_check --clean --if-exists --no-owner flow_analysis_pre_3_0_9.dump
   ```

5. 在恢复库校验行数、表、外键和 CHECK 约束；例如：

   ```sql
   SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname;
   SELECT conrelid::regclass, conname, contype
   FROM pg_constraint
   WHERE connamespace = 'public'::regnamespace
   ORDER BY conrelid::regclass::text, conname;
   ```

6. DBA 审核 `docs/database_role_grants.sql`，并在单独变更单中配置最小权限应用账号与
   migration 账号。桌面客户端不得继续使用管理员账号。
7. 先运行迁移计划（不带 `--apply`），确认目标名称和 SQL 列表：

   ```powershell
   python migrations/run_migrations.py --database flow_analysis --confirm-database flow_analysis
   ```

8. 仅在正式批准、旧客户端完全停止、备份恢复验证完成后，以 migration 专用环境变量
   执行 `--apply`。该入口会将全部 001~005 放进一个事务；任一 SQL 失败会回滚。
9. 用只读 SQL 校验 004/005 的表、列、外键、CHECK 约束和升级前行数，再以新版客户端
   做单一 canary 验证。

## 失败处理

- 事务尚未提交：migration CLI 的单事务会自动回滚；保持维护窗口，记录失败事件。
- 已提交但验收失败：停止新客户端，保留证据；只有经过批准才可以在隔离环境验证后恢复
  备份。不要在生产库上临时手工删列/删表“修复”。
- 旧客户端风险：旧版标签无条件 UPSERT 不理解 `revision`、`is_active` 和人工管理审计。
  因此严禁旧版与 005 后新版混跑；必须统一维护窗口切换。

## 迁移审计

`migrations/run_migrations.py` 默认只输出 JSON 计划；实际执行必须显式传入
`--apply`，并让 `--database` 与 `--confirm-database` 完全一致。输出只包含目标库名、
migration 文件名、时间和状态，绝不包含主机、用户、密码、连接字符串或 SQL 正文。
