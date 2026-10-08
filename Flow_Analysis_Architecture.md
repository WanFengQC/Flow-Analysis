# Flow Analysis 项目架构说明

## 1. 文档优先级与迁移原则

本文件是 Flow Analysis 当前正式架构的唯一基准，优先级高于 `ai-coding.md`：

~~~
Flow_Analysis_Architecture.md
> ai-coding.md
~~~

当用户已经明确确认正式架构迁移时，必须同步更新本文件和 `ai-coding.md`。不得引用历史规则、历史实现或历史数据库方案，将项目重新改回已经废弃的方案。

当前正式数据库方案为 PostgreSQL，数据访问使用 psycopg 3，连接池使用 `psycopg_pool.AsyncConnectionPool`。

Windows 内网自动更新使用 Ed25519 签名清单，客户端通过 `cryptography` 验签；私钥始终离线保管。

## 2. 当前正式项目结构

~~~
flow-analysis/
├── main.py
├── config/
│   ├── settings.py
│   └── database_settings.py
├── infrastructure/
│   ├── __init__.py
│   ├── async_runtime.py
│   ├── application_runtime.py
│   ├── update_service.py
│   └── update_launcher.py
├── controllers/
│   ├── __init__.py
│   └── main_controller.py
├── views/
│   ├── __init__.py
│   └── main_window.py
├── workers/
│   ├── __init__.py
│   ├── analysis_worker.py
│   └── cookie_worker.py
├── services/
│   ├── __init__.py
│   ├── analysis_service.py
│   ├── data_service.py
│   ├── export_service.py
│   ├── sellersprite_service.py
│   └── wearesellers_service.py
├── models/
│   ├── __init__.py
│   ├── account.py
│   └── analysis_result.py
├── repositories/
│   ├── __init__.py
│   ├── database.py
│   ├── base_repository.py
│   └── exceptions.py
├── ui/
│   ├── main_window.ui
│   └── ui_main_window.py
├── utils/
│   ├── __init__.py
│   ├── config.py
│   └── logger.py
├── data/
│   └── exports/
├── packaging/
│   ├── FlowAnalysis.iss
│   ├── build_release.py
│   ├── create_update_manifest.py
│   ├── generate_update_signing_key.py
│   ├── verify_update_release.py
│   └── update-server/default.conf
├── requirements.txt
├── requirements-build.txt
├── Flow_Analysis_Architecture.md
└── ai-coding.md
~~~

## 3. 正式运行时架构

~~~
Qt UI 主线程
    ├── MainWindow / View
    └── MainController
            ├── QThread + Worker
            │       └── 同步阻塞型后台任务
            └── ApplicationRuntime
                    ├── AsyncRuntime
                    │       └── asyncio EventLoop
                    │               └── PostgreSQL AsyncConnectionPool
                    └── UpdateService
                            └── 同一 AsyncRuntime 中长期复用的 HTTP AsyncClient
~~~

一个应用实例在正常情况下只维护一个 `ApplicationRuntime`、一个 `AsyncRuntime`、一个 PostgreSQL 连接池和一个 UpdateService HTTP Client。它们由应用启动过程统一创建和注入，不能由业务对象按需重复创建。

`main.py` 可以负责创建 `ApplicationRuntime` 并注入 `MainController`，但不得包含 SQL、数据库业务规则或其他数据库业务逻辑。

## 4. 线程与异步 I/O 职责

三种运行位置可以共存，但职责必须明确，不能随意交叉或嵌套：

| 运行位置 | 负责内容 | 禁止内容 |
| --- | --- | --- |
| Qt 主线程 | View、界面事件、Controller 协调、界面状态更新 | 阻塞网络、阻塞数据库、长时间计算 |
| QThread + Worker | 同步阻塞型后台任务 | 直接操作 View、创建独立连接池 |
| AsyncRuntime | PostgreSQL 等异步 I/O 的调度与生命周期 | UI 操作、业务决策、每个请求创建 EventLoop |

Windows 下 `AsyncRuntime` 必须使用与 psycopg 异步模式兼容的 `SelectorEventLoop`，不使用 `ProactorEventLoop`。

正常运行期间，禁止在 Qt UI 主线程中阻塞等待 `Future.result()`。异步任务完成后必须通过 callback 或 Qt Signal 回到 Controller，再由 Controller 调用 View 暴露的方法更新界面。只有程序退出的资源清理阶段，才允许有明确超时限制的同步等待。

应用退出顺序固定为：

~~~
停止业务任务
→ 取消更新下载并关闭 UpdateService HTTP Client
→ 关闭 PostgreSQL 连接池
→ 停止 AsyncRuntime
→ Qt 退出
~~~

## 5. 分层职责

### 5.1 View

`views/main_window.py` 只负责 UI 初始化、自定义 UI 行为、界面状态更新，以及向 Controller 暴露必要的方法。View 不得包含网络请求、Cookie 获取、数据库访问、数据分析或业务决策。

### 5.2 Controller

`MainController` 负责协调 View、Worker、Service 和应用运行时，处理用户操作后的流程编排。Controller 通过 View 暴露的方法更新界面，不直接操作 Qt Designer 生成文件中的控件实现细节。

### 5.3 Worker

Worker 仅承载需要放到 `QThread` 执行的同步阻塞型任务，并使用 Signal 把进度、结果和错误交回 Controller。Worker 不直接操作 UI，不持有独立数据库连接池。

### 5.4 Service

Service 负责业务规则、跨来源数据编排和跨 Repository 的事务边界。Service 不处理 UI 控件，不创建连接池，也不将基础设施生命周期逻辑混入业务实现。

### 5.5 Repository

Repository 只负责 PostgreSQL 数据访问和数据映射，不负责业务规则、UI 或外部 API。

- 普通单次操作可自行通过 `DatabaseManager.connection()` 从连接池取得连接。
- 事务场景必须支持复用外部传入的同一个 connection。
- Repository 不提供全局 `commit()` 或 `rollback()` 方法。
- PostgreSQL Row 不应长期跨层传播；Repository 对外优先返回 `models/` 中明确的数据 Model。

事务边界只能由 Service 决定：

~~~
Service
→ DatabaseManager.transaction()
→ 同一个 connection
   ├── Repository A
   ├── Repository B
   └── Repository C
~~~

事务正常完成时自动 COMMIT，任意异常时自动 ROLLBACK；多个 Repository 必须复用该事务提供的同一个 connection。

### 5.6 Model

Model 只描述稳定的数据结构和字段语义，不包含 SQL、UI 或连接管理逻辑。Repository 负责将 PostgreSQL 查询结果转换为 Model，不能把裸 `dict`、`psycopg.Row` 作为长期的上层接口。

### 5.7 Infrastructure

`infrastructure/` 仅负责 EventLoop、应用运行时和基础设施生命周期，不允许放置业务逻辑：

~~~
infrastructure/
├── async_runtime.py          # AsyncRuntime 与 asyncio EventLoop 的创建、提交、停止
├── application_runtime.py    # 应用级基础设施的创建、启动、关闭和依赖注入
├── update_service.py          # 签名更新清单校验、安装包校验下载和更新生命周期
└── update_launcher.py         # 独立进程等待主程序退出、静默安装并重启
~~~

`AsyncRuntime` 负责异步 I/O 的 EventLoop 生命周期；`ApplicationRuntime` 负责统一持有并关闭 AsyncRuntime、PostgreSQL 连接池和 UpdateService。更新模块只能处理发布物与应用生命周期，不能读取、上传或承载业务数据。

## 6. PostgreSQL 与连接池规则

`repositories/database.py` 中的 `DatabaseManager` 是 PostgreSQL 连接池和连接上下文的统一入口。Repository、Service、Worker 均不得各自创建连接池。

禁止以下行为：

- 每条 SQL 新建连接池。
- 每条 SQL 新建 EventLoop 或线程。
- 在 Repository 内决定跨多个写操作的事务边界。
- 在任意层暴露全局 `commit()`、`rollback()` 接口。

连接池配置必须由 `config/database_settings.py` 集中管理，并明确控制：

- `pool_min_size`
- `pool_max_size`
- 等待队列
- 连接获取与操作超时

设计时必须评估 PostgreSQL 总连接数：

~~~
客户端实例数量 × 单实例 pool_max_size
~~~

该上限必须与 PostgreSQL 可承载连接数、保留管理连接和其他应用连接共同评估。

数据库凭据允许随程序配置保存，但数据库密码不得输出到日志、异常信息或 UI。

## 7. 数据库异常与重试

psycopg / psycopg_pool 的原始异常必须在数据库基础设施边界统一转换为项目数据库异常，异常定义集中在 `repositories/exceptions.py`。

- 不得静默吞掉数据库异常。
- 不得把所有数据库异常粗暴转换为同一个没有信息量的错误。
- 转换后的异常应保留可诊断的类别和上下文，同时不得泄露数据库密码等敏感信息。

禁止对所有数据库操作实施统一自动重试。未证明幂等性前，不允许对 `INSERT`、`UPDATE`、`DELETE` 进行通用自动重试。重试必须按异常类型、操作语义和幂等性单独设计。

## 8. 配置职责

~~~
config/settings.py
→ 通用程序配置

config/database_settings.py
→ PostgreSQL 与连接池配置
~~~

配置读取、校验和默认值处理应保持集中，业务模块不应散落读取环境配置或重复组装连接参数。

`settings.py` 中的更新配置仅包含清单 URL、频道、版本、下载上限和 Ed25519 公钥；更新私钥只能离线保存，严禁写入仓库、安装包、日志或更新服务器。

## 9. Qt Designer 规则

当前项目已经正式启用 Qt Designer：

- `ui/main_window.ui` 是设计源文件。
- `ui/ui_main_window.py` 由 `pyside6-uic` 自动生成。
- 严禁手工修改 `ui/ui_main_window.py`。
- 自定义 UI 行为必须放在 `views/main_window.py`。
- Controller 只能调用 View 暴露的方法，不直接操作生成控件。

修改 `.ui` 后必须使用项目虚拟环境重新生成对应 Python 文件，再进行界面验证。

## 10. 通用开发约束

- 保留 View / Controller / Worker / Service / Repository 的边界，不跨层堆放职责。
- 正式代码使用清晰的中文注释，解释设计意图和关键边界。
- 单次只推进一个功能点；未被当前需求授权的重构不应顺带进行。
- 新增功能先确认所属层，再确定线程、异步 I/O 和数据库访问边界。
- 日志用于可诊断性，不能记录密码、Cookie、Token 等敏感信息。

## 11. Windows 发布与内网自动更新

Windows 正式发布物使用 PyInstaller `onedir` 应用、独立更新器和 Inno Setup 安装包。`packaging/build_release.py` 是唯一正式构建入口；构建依赖单独定义在 `requirements-build.txt`，不得混入普通运行时安装流程。

更新服务器只提供签名清单和安装包。客户端启动后只在冻结发布环境中，经 `AsyncRuntime` 后台检查更新；不得阻塞 Qt UI 主线程。仅当清单满足频道一致、版本严格高于当前版本、Ed25519 签名有效，且安装包 SHA-256 与大小均验证通过时，才允许启动更新。

下载完成后，如果存在分析、导出或审核等活动任务，必须等待安全边界再更新；主程序退出前必须显示不可取消的更新交接提示，独立更新器等待主程序退出后以可见的安装进度窗口安装并重启。禁止降级、禁止覆盖运行中的主程序、禁止因更新失败中断当前业务任务。内网 HTTP 传输仅因客户端签名校验而被允许，不能省略签名、哈希或大小校验。

更新器必须从安装目录外的用户更新缓存运行，并通过 Windows 全局互斥锁保证任意时刻只有一个更新器可执行安装与重启；主程序同一进程内也只能启动一次更新流程。这样安装器不会锁住待替换的更新器文件，也不会因重复回调出现多次关闭或重启。
