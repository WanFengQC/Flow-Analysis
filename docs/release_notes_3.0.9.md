# Flow Analysis 3.0.9 发布准备说明

本版本保持 SellerSprite、Word Analysis、Normalization、AI Tagging、Amazon 和 Excel
导出业务逻辑不变，新增的是发布前安全整改：

- 数据库连接改为仅从外部运行时配置读取，源码与 EXE 内置运行配置均不提供数据库密码。
- migration CLI 默认仅输出计划；实际 DDL 需要 `--apply` 和目标数据库双重确认。
- 001~005 migration 在同一个事务中执行，失败会回滚。
- 增加数据库角色隔离授权蓝图与逻辑备份/隔离恢复运行手册。
- 将可重复的 35 个自动化测试源码纳入版本管理，同时继续排除测试产物和敏感样本。

本说明不代表已发布。正式数据库 migration、安装包构建、自动更新发布和任何账号权限
调整均需后续明确批准。
