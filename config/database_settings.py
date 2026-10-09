"""Flow Analysis 的 PostgreSQL 运行时连接配置。

源码绝不保存数据库密码，也不为桌面客户端提供管理员账号默认值。安装后的
应用只能从用户配置目录 .env 或进程环境变量读取专用应用账号配置。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

# 导入 settings 的唯一目的，是复用其已完成的 .env 加载顺序：进程环境变量
# 优先，其次是安装后 AppData/.env，源码启动时才兼容项目根目录 .env。
from config import settings as application_settings


_ENV_PREFIX = "FLOW_ANALYSIS_DB_"
_EXTERNAL_OVERRIDE_ENV = "FLOW_ANALYSIS_ENABLE_EXTERNAL_DB_OVERRIDE"


def _environment_text(name: str, default: str = "") -> str:
    """读取数据库运行配置，不接受任何 EXE 内置配置作为后备。"""

    return os.getenv(f"{_ENV_PREFIX}{name}", default).strip()


def _environment_integer(name: str, default: int) -> int:
    """读取整数配置并给出不包含敏感值的明确错误。"""

    raw_value = _environment_text(name)
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"环境变量 {_ENV_PREFIX}{name} 必须是整数"
        ) from exc


def _config_integer(
    value: str,
    variable_name: str,
    default: int,
) -> int:
    """解析内嵌配置中的整数，报错时不回显实际配置值。"""

    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"内嵌配置 {variable_name} 必须是整数") from exc


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """PostgreSQL 数据库及连接池配置。"""

    # 所有基础连接信息均无生产默认值，避免源码或 migration CLI 静默命中正式库。
    host: str = ""
    port: int = 5432
    database: str = ""
    user: str = ""
    password: str = ""
    sslmode: str = "prefer"

    # 建立 PostgreSQL TCP 连接的最长等待时间，单位：秒。
    connect_timeout: int = 5

    # 单条 SQL 的最大执行时间，单位：毫秒。
    statement_timeout_ms: int = 30_000

    # PostgreSQL 连接池配置。
    pool_min_size: int = 1
    pool_max_size: int = 5
    pool_max_waiting: int = 50
    pool_timeout: float = 10.0
    pool_max_lifetime: float = 3600.0
    pool_max_idle: float = 300.0
    pool_reconnect_timeout: float = 30.0

    @classmethod
    def from_runtime_environment(cls) -> "DatabaseSettings":
        """按运行形态构造应用数据库配置。

        源码开发始终支持环境变量与 .env。冻结正式包默认只读取构建时内嵌
        的五项数据库字段；只有显式设置测试/开发覆盖开关时才接受外部替换。
        """

        frozen_runtime = bool(getattr(sys, "frozen", False))
        external_override_enabled = (
            os.getenv(_EXTERNAL_OVERRIDE_ENV, "").strip() == "1"
        )
        if frozen_runtime and not external_override_enabled:
            packaged = application_settings.packaged_database_runtime_config()
            return cls(
                host=packaged["FLOW_ANALYSIS_DB_HOST"],
                port=_config_integer(
                    packaged["FLOW_ANALYSIS_DB_PORT"],
                    "FLOW_ANALYSIS_DB_PORT",
                    5432,
                ),
                database=packaged["FLOW_ANALYSIS_DB_NAME"],
                user=packaged["FLOW_ANALYSIS_DB_USER"],
                password=packaged["FLOW_ANALYSIS_DB_PASSWORD"],
            )

        # 显式引用可防止代码清理时误删上方导入，破坏开发期 .env 加载顺序。
        _ = application_settings.APP_CONFIG_DIR
        return cls(
            host=_environment_text("HOST"),
            port=_environment_integer("PORT", 5432),
            database=_environment_text("NAME"),
            user=_environment_text("USER"),
            password=_environment_text("PASSWORD"),
            sslmode=_environment_text("SSLMODE", "prefer"),
            connect_timeout=_environment_integer("CONNECT_TIMEOUT", 5),
            statement_timeout_ms=_environment_integer(
                "STATEMENT_TIMEOUT_MS", 30_000
            ),
        )

    def validate(self) -> None:
        """检查配置是否合法，错误信息绝不回显连接字符串或密码。"""

        required_fields = {
            "HOST": self.host,
            "NAME": self.database,
            "USER": self.user,
            "PASSWORD": self.password,
        }
        missing = [name for name, value in required_fields.items() if not value]
        if missing:
            raise ValueError(
                "缺少数据库运行时配置："
                + ", ".join(f"{_ENV_PREFIX}{name}" for name in missing)
            )

        if not 1 <= self.port <= 65535:
            raise ValueError("数据库端口不合法")

        if self.pool_min_size < 0:
            raise ValueError("pool_min_size 不能小于 0")

        if self.pool_max_size <= 0:
            raise ValueError("pool_max_size 必须大于 0")

        if self.pool_min_size > self.pool_max_size:
            raise ValueError("pool_min_size 不能大于 pool_max_size")

        if self.pool_timeout <= 0:
            raise ValueError("pool_timeout 必须大于 0")

        if self.connect_timeout <= 0:
            raise ValueError("connect_timeout 必须大于 0")

        if self.statement_timeout_ms <= 0:
            raise ValueError("statement_timeout_ms 必须大于 0")

        if self.pool_max_waiting <= 0:
            raise ValueError("pool_max_waiting 必须大于 0")


# 整个程序统一使用运行时加载的应用账号配置。导入模块本身不做网络连接，
# 也不在这里校验，便于无数据库的单元测试和 --packaging-smoke 安全启动。
DATABASE_SETTINGS = DatabaseSettings.from_runtime_environment()
