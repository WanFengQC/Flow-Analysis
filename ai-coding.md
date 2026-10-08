# Flow Analysis AI Coding Rules

## 1. 规则优先级

开始修改前必须阅读 `Flow_Analysis_Architecture.md`。出现冲突时，按以下优先级执行：

~~~
Flow_Analysis_Architecture.md
> ai-coding.md
> 当前用户需求
> 历史实现和历史注释
~~~

当用户已经明确确认正式架构迁移时，必须同步更新架构文档和本规则；不得使用历史规则或历史实现把项目重新改回废弃方案。

## 2. 当前技术基线

- 桌面 UI：Python + PySide6 + Qt Widgets。
- UI 设计：Qt Designer。
- 正式数据库：PostgreSQL。
- PostgreSQL 驱动：psycopg 3。
- 连接池：`psycopg_pool.AsyncConnectionPool`。
- 数据访问：Repository 模式，不新增 ORM。
- 自动更新签名校验：`cryptography` 的 Ed25519；仅客户端公钥允许进入发布物。

不得未经用户授权替换上述技术基线、引入新的 UI 框架或新增依赖。

## 3. 当前目录职责

~~~
main.py                              # 程序入口、ApplicationRuntime 创建与 Controller 注入
config/settings.py                   # 通用程序配置
config/database_settings.py          # PostgreSQL 与连接池配置
infrastructure/async_runtime.py      # asyncio EventLoop 生命周期
infrastructure/application_runtime.py# 应用基础设施生命周期
infrastructure/update_service.py     # 已签名更新清单、安装包下载与校验
infrastructure/update_launcher.py    # 独立更新进程：等待退出、静默安装、重启
controllers/main_controller.py       # UI 流程协调
views/main_window.py                 # View 层与自定义 UI 行为
workers/                             # QThread 中的同步阻塞任务
services/                            # 业务规则、任务编排与事务边界
models/                              # 数据结构 Model
repositories/database.py             # PostgreSQL 连接池、连接和事务入口
repositories/base_repository.py      # Repository 公共数据访问能力
repositories/exceptions.py           # 项目数据库异常
ui/main_window.ui                    # Qt Designer 设计源文件
ui/ui_main_window.py                 # pyside6-uic 自动生成文件
packaging/                           # PyInstaller、Inno Setup、签名清单与更新服务器配置
requirements-build.txt               # 仅发布构建所需依赖
~~~

`main.py` 可以创建 `ApplicationRuntime` 并将其注入 `MainController`，但不得编写 SQL、数据库业务规则或其他数据库业务逻辑。

`infrastructure/` 只负责 EventLoop、应用运行时和基础设施生命周期，不允许放置业务逻辑。UpdateService 只处理签名更新清单、发布物下载校验和更新生命周期；它不得读取、上传或处理业务数据。

## 4. 运行时、线程与异步 I/O

正式运行时关系如下：

~~~
Qt UI 主线程
    ├── MainWindow / View
    └── MainController
            ├── QThread + Worker
            │       └── 同步阻塞型后台任务
            └── ApplicationRuntime
                    └── AsyncRuntime
                            └── asyncio EventLoop
                                    └── PostgreSQL AsyncConnectionPool
~~~

一个应用实例正常情况下只能维护一个 `ApplicationRuntime`、一个 `AsyncRuntime` 和一个 PostgreSQL 连接池。不得让 Repository、Service 或 Worker 各自创建连接池；不得为每条 SQL 新建连接池、EventLoop 或线程。

职责边界：

- Qt 主线程：UI、用户事件、Controller 协调和界面更新。
- QThread：同步阻塞型后台任务。
- AsyncRuntime：异步 I/O。

三者职责必须明确，不得随意交叉或嵌套。Windows 下 AsyncRuntime 必须使用兼容 psycopg 异步模式的 `SelectorEventLoop`，不使用 `ProactorEventLoop`。

正常运行时，严禁在 Qt UI 主线程阻塞等待 `Future.result()`。异步任务结果必须通过 callback 或 Qt Signal 回到 Controller，再由 Controller 调用 View 的公开方法更新界面。仅在程序退出的资源清理阶段，允许使用带有限超时的同步等待。

退出流程必须遵守：

~~~
停止业务任务
→ 取消更新下载并关闭 UpdateService HTTP Client
→ 关闭 PostgreSQL 连接池
→ 停止 AsyncRuntime
→ Qt 退出
~~~

## 5. View 与 Qt Designer

- `ui/main_window.ui` 是正式设计源文件。
- `ui/ui_main_window.py` 必须由 `pyside6-uic` 自动生成，严禁手工修改。
- 自定义 UI 行为、控件组装和 View 状态更新放在 `views/main_window.py`。
- 修改 `.ui` 后，使用项目虚拟环境重新生成对应 Python 文件。
- Controller 只能调用 View 暴露的方法，不能直接操作生成控件。
- View 不得包含网络请求、Cookie 获取、数据库访问、数据分析或业务规则。
- 所有界面控件使用 Layout 管理，禁止依赖绝对坐标。

## 6. Controller 与 Worker

Controller 负责协调 View、Worker、Service 和应用运行时：

- 接收 View 事件并编排流程。
- 启动、停止和连接 Worker 的 Signal。
- 接收异步 callback 或 Worker Signal 后更新 View。
- 不直接编写 UI 细节，不直接承担数据库访问实现。

Worker 只负责 QThread 中的同步阻塞型后台任务：

- 不得直接操作 View。
- 使用 Signal 返回进度、结果和异常。
- 不得持有或创建独立 PostgreSQL 连接池。
- Worker 完成后必须正确退出并释放 QThread 资源。

## 7. Service

Service 负责业务规则、外部数据编排和跨 Repository 的事务边界：

- 不操作 UI 控件。
- 不创建 PostgreSQL 连接池、EventLoop 或线程。
- 不把数据库连接、SQL 和底层异常转换细节泄漏为上层业务接口。
- 跨多个写操作时，由 Service 显式决定事务边界。

事务调用关系：

~~~
Service
→ DatabaseManager.transaction()
→ 同一个 connection
   ├── Repository A
   ├── Repository B
   └── Repository C
~~~

在事务中，所有参与的 Repository 必须复用外部传入的同一个 connection。正常完成时自动 COMMIT，任意异常时自动 ROLLBACK。

## 8. Repository、Model 与数据库访问

Repository 只负责 PostgreSQL 数据访问和数据映射：

- 不负责业务规则、UI 或外部 API。
- 普通单次操作可以自行从 `DatabaseManager.connection()` 取得连接。
- 事务操作必须支持传入外部 connection 并复用它。
- 不提供全局 `commit()` 或 `rollback()` 方法。
- 不在 Repository 内决定跨多个写操作的事务边界。

Model 只描述数据结构：

- PostgreSQL Row 不应长期跨层传播。
- Repository 对外优先返回明确的 `models/` 数据 Model，而不是裸 `dict` 或 `psycopg.Row`。
- Model 不包含 SQL、UI 或连接管理逻辑。

数据库异常规则：

- psycopg / psycopg_pool 原始异常必须在数据库基础设施边界转换为 `repositories/exceptions.py` 中的项目数据库异常。
- 不得静默吞异常。
- 不得将所有数据库异常粗暴转换为同一个无信息量错误。
- 错误信息需要保留可诊断的异常类别与必要上下文，同时不得泄露密码等敏感信息。

## 9. 连接池与重试

连接池参数集中在 `config/database_settings.py`，设计和实现必须控制：

- `pool_min_size`
- `pool_max_size`
- 等待队列
- 连接获取超时与操作超时

评估连接数时，必须考虑：

~~~
客户端实例数量 × 单实例 pool_max_size
~~~

该数量必须与 PostgreSQL 的总连接上限、保留连接及其他应用负载一起评估。

禁止对所有数据库操作统一自动重试。未保证幂等性前，不允许对 `INSERT`、`UPDATE`、`DELETE` 做通用自动重试。是否重试必须基于异常类型、操作语义和幂等性单独设计。

数据库连接凭据可随程序配置保存，但数据库密码不得出现在日志、异常文本、UI 或调试输出中。

## 10. 代码质量与边界

- 正式代码必须有清晰、准确的中文注释；注释解释意图、边界和非显而易见的原因，不重复代码字面含义。
- 单次只推进一个功能点，不顺带重构无关模块。
- 新功能先确定归属层，再编写实现；禁止把临时逻辑跨层堆入 View、Controller 或 Worker。
- 不新增测试按钮、Demo 控件、临时演示逻辑或未确定的业务功能。
- 日志用于诊断，不记录 Cookie、Token、密码及其他敏感信息。
- 修改时保留既有有效的 View / Controller / Worker / Service / Repository 分层边界。

## 11. Windows 发布与内网自动更新

- 正式 Windows 发布使用 PyInstaller `onedir` 应用、独立更新器和 Inno Setup 安装包；只允许通过 `packaging/build_release.py` 生成发布物。
- `requirements-build.txt` 只用于构建环境，运行时依赖继续由 `requirements.txt` 管理。
- 更新清单必须包含频道、严格递增版本、安装包 URL、SHA-256、大小和 Ed25519 签名；客户端在下载、安装前必须逐项校验。
- Ed25519 私钥只能离线保存，不得写入源码、`.env`、安装包、日志、更新服务器或 UI；客户端仅内置公钥。
- 更新检查与下载必须在 AsyncRuntime 后台执行，禁止阻塞 Qt UI 主线程；应用未冻结运行或当前版本已是最新时不得下载。
- 自动更新必须等待分析、导出、审核等活动任务结束。主程序退出前必须显示不可取消的更新交接提示；独立更新器等待主程序退出后保留可见的安装进度窗口并重启，禁止使用会隐藏进度的完全静默安装参数。禁止降级、禁止覆盖正在运行的主程序、禁止因更新失败终止业务任务。
- 更新器必须从安装目录外的用户更新缓存运行，并使用 Windows 全局互斥锁确保安装与重启只执行一次；主程序同一进程内也必须防止重复派生更新器。
- 内网 HTTP 只能作为传输层；不得因为在内网而跳过签名、SHA-256 或文件大小校验。

## 12. 修改前检查

编码前应确认：

1. 当前功能属于哪个分层。
2. 是否需要 QThread，或应提交给 AsyncRuntime。
3. 是否涉及 PostgreSQL 连接、事务、异常转换或 Model 映射。
4. 是否应通过 Service 协调多个 Repository。
5. 是否只修改当前需求授权的文件和功能点。
