from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """PostgreSQL 数据库及连接池配置。"""

    # =========================
    # PostgreSQL 基础连接配置
    # =========================

    # PostgreSQL 服务器地址。
    host: str = "192.168.110.107"

    # PostgreSQL 服务端口。
    port: int = 5432

    # Flow Analysis 独立使用的新 PostgreSQL 数据库；业务表后续按正式 Schema 建立。
    database: str = "flow_analysis"

    # 数据库用户名。
    user: str = "postgres"

    # 数据库密码。
    # 当前项目接受将数据库凭据随 EXE 一起打包。
    password: str = "123456"

    # PostgreSQL SSL 模式。
    sslmode: str = "prefer"

    # =========================
    # 连接超时配置
    # =========================

    # 建立 PostgreSQL TCP 连接的最长等待时间，单位：秒。
    connect_timeout: int = 5

    # 单条 SQL 的最大执行时间，单位：毫秒。
    statement_timeout_ms: int = 30_000

    # =========================
    # PostgreSQL 连接池配置
    # =========================

    # 连接池保持的最小连接数。
    pool_min_size: int = 1

    # 单个 Flow Analysis 客户端允许使用的最大数据库连接数。
    pool_max_size: int = 5

    # 当连接池已满时，最多允许多少个任务等待连接。
    # 超过这个数量后直接拒绝新的数据库请求，防止请求无限堆积。
    pool_max_waiting: int = 50

    # 连接池没有空闲连接时，任务最多等待多久，单位：秒。
    pool_timeout: float = 10.0

    # 一个连接允许在池中存活的最长时间，单位：秒。
    pool_max_lifetime: float = 3600.0

    # 空闲连接允许保持的最长时间，单位：秒。
    pool_max_idle: float = 300.0

    # 连接池发生连接错误后，最多持续尝试恢复多久，单位：秒。
    pool_reconnect_timeout: float = 30.0

    def validate(self) -> None:
        """检查数据库配置是否合法。"""

        if not self.host:
            raise ValueError("数据库 host 不能为空")

        if not 1 <= self.port <= 65535:
            raise ValueError(
                f"数据库端口不合法：{self.port}"
            )

        if not self.database:
            raise ValueError("数据库名称不能为空")

        if not self.user:
            raise ValueError("数据库用户名不能为空")

        if not self.password:
            raise ValueError("数据库密码不能为空")

        if self.pool_min_size < 0:
            raise ValueError(
                "pool_min_size 不能小于 0"
            )

        if self.pool_max_size <= 0:
            raise ValueError(
                "pool_max_size 必须大于 0"
            )

        if self.pool_min_size > self.pool_max_size:
            raise ValueError(
                "pool_min_size 不能大于 pool_max_size"
            )

        if self.pool_timeout <= 0:
            raise ValueError(
                "pool_timeout 必须大于 0"
            )

        if self.connect_timeout <= 0:
            raise ValueError(
                "connect_timeout 必须大于 0"
            )

        if self.statement_timeout_ms <= 0:
            raise ValueError(
                "statement_timeout_ms 必须大于 0"
            )

        if self.pool_max_waiting <= 0:
            raise ValueError(
                "pool_max_waiting 必须大于 0"
            )


# 整个程序统一使用这一份数据库配置。
DATABASE_SETTINGS = DatabaseSettings()

# 程序启动时尽早检查配置是否合法。
DATABASE_SETTINGS.validate()
