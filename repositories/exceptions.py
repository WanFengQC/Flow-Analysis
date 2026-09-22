"""Flow Analysis 数据库层统一异常定义。"""


class DatabaseError(Exception):
    """Flow Analysis 数据库异常基类。"""


class DatabaseConnectionError(DatabaseError):
    """PostgreSQL 连接建立失败或连接已经失效。"""


class DatabaseAuthenticationError(DatabaseError):
    """PostgreSQL 身份认证失败。"""


class DatabasePoolTimeoutError(DatabaseError):
    """连接池繁忙，等待连接超时。"""


class DatabaseQueryTimeoutError(DatabaseError):
    """SQL 执行超过允许的最长时间。"""


class DatabaseQueryError(DatabaseError):
    """SQL 执行失败。"""


class DatabaseUnavailableError(DatabaseError):
    """数据库暂时不可用。"""

class DatabasePoolBusyError(DatabaseError):
    """数据库连接池等待队列已满，当前请求被拒绝。"""