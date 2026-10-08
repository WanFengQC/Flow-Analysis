import json
import os
import sys
from pathlib import Path
from typing import Any


APP_NAME = "Flow Analysis"

# Windows 下，安装包自身可随版本目录整体替换；用户配置、缓存和下载的
# 更新安装包必须位于安装目录之外，升级才不会覆盖它们。
APP_DATA_DIR = Path(
    os.getenv("LOCALAPPDATA", Path.home())
) / APP_NAME
APP_CONFIG_DIR = Path(
    os.getenv("APPDATA", Path.home())
) / APP_NAME

# Excel 导出模板是只含结构与样式的发布资源。源码和 PyInstaller 目录应用均
# 从同一相对位置加载，不能指向某一次用户分析生成的工作簿。
APPLICATION_RESOURCE_DIR = Path(
    getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)
)
EXPORT_TEMPLATE_PATH = (
    APPLICATION_RESOURCE_DIR
    / "resources"
    / "excel_templates"
    / "analysis_export_template.xlsx"
)

# 产品资料是 AI 打标的只读业务背景。发布包必须携带这两份资料；源码开发时
# 若尚未准备发布资源，则仍允许从用户下载目录读取，兼容既有本地工作流。
PRODUCT_KNOWLEDGE_RESOURCE_DIR = (
    APPLICATION_RESOURCE_DIR / "resources" / "product_knowledge"
)
PRODUCT_KNOWLEDGE_DOCUMENT_NAMES = ("产品资料-U.txt", "产品资料-M.txt")


def _load_env_file(env_path: Path) -> None:
    """以最小 dotenv 语义读取一个 .env，且不覆盖进程环境变量。"""

    # 本项目不新增 dotenv 依赖；配置只需支持 KEY=VALUE、空行、注释与简单
    # 引号值。系统/启动器已注入的环境变量优先，便于生产环境安全覆盖。
    try:
        content = env_path.read_text(encoding="utf-8")
    except OSError:
        # .env 只用于本地启动便利；缺失时仍由后续配置校验给出明确提示。
        return

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        if (
            len(value) >= 2
            and value[0] in {"\"", "'"}
            and value[-1] == value[0]
        ):
            value = value[1:-1]
        os.environ.setdefault(name, value)


def _load_application_env() -> None:
    """加载开发期与安装后的用户 .env，并保持外部环境变量最高优先级。"""

    # 源码启动时保留项目根目录 .env 的便利性；安装包不读取安装目录，避免
    # 更新意外覆盖或携带任何密钥文件。
    if not getattr(sys, "frozen", False):
        _load_env_file(Path(__file__).resolve().parent.parent / ".env")

    # 安装后的用户配置固定在 Roaming AppData，升级包不会写入该目录。
    _load_env_file(APP_CONFIG_DIR / ".env")


# 必须早于任何 os.getenv 配置读取，确保本地 .env 在导入 settings 时生效。
_load_application_env()


# 发布版本只由源码维护；更新清单只能提供更高的正式语义化版本。
APP_VERSION = "3.0.7"

# Cookie 获取接口
COOKIE_API_URL = "http://192.168.110.107:18765/api/cookie"

# HTTP 请求超时时间，单位：秒
REQUEST_TIMEOUT = 10.0

# SellerSprite API 客户端的统一服务地址。
# 具体业务接口路径由 ApiService 管理，不在配置层逐个定义。
SELLERSPRITE_BASE_URL = "https://www.sellersprite.com"

# Amazon Product Summary fallback 使用独立 persistent Chromium Profile，绝不
# 使用用户日常 Chrome/Edge Profile，避免锁冲突和浏览器状态互相污染。
AMAZON_PLAYWRIGHT_PROFILE_DIR = APP_DATA_DIR / "amazon_playwright_profile"
AMAZON_ZIP_CODE = os.getenv("AMAZON_ZIP_CODE", "10001").strip() or "10001"

# Cookie 本地缓存文件路径
COOKIE_CACHE_FILE = APP_DATA_DIR / "cookie.json"

# 用于验证 Cookie 是否有效的业务接口
SELLERSPRITE_ME_URL = "https://www.sellersprite.com/v2/me"


# UniAPI 使用 OpenAI-compatible 路由承载三家 AI Tagging Provider。当前正式
# 桌面发布版内置一套项目专用默认配置，确保首次安装无需手动创建 .env；运行
# 环境变量仍可覆盖，便于后续轮换专用 Key。该值绝不能输出到日志、异常或 UI。
UNIAPI_BASE_URL = "https://api.uniapi.io/v1"
UNIAPI_API_KEY = os.getenv("UNIAPI_API_KEY", "").strip()

# 下列值必须由 UniAPI 当前模型列表或控制台配置后提供。
# 业务显示名称与真实 model_id 分离，避免把显示名称误当作请求参数。
TAGGING_GPT_MODEL = (
    os.getenv("TAGGING_GPT_MODEL", "").strip() or "gpt-6-sol"
)
TAGGING_CLAUDE_MODEL = (
    os.getenv("TAGGING_CLAUDE_MODEL", "").strip() or "claude-sonnet-5"
)
TAGGING_GEMINI_MODEL = (
    os.getenv("TAGGING_GEMINI_MODEL", "").strip() or "gemini-3.8-flash"
)

# 推理档位是业务配置；是否向 UniAPI 请求体传递具体参数由各 Provider 的
# request options 明确决定，不能把同一不兼容参数强加给全部模型。
TAGGING_GPT_REASONING_PROFILE = os.getenv(
    "TAGGING_GPT_REASONING_PROFILE",
    "medium",
).strip()
TAGGING_CLAUDE_REASONING_PROFILE = os.getenv(
    "TAGGING_CLAUDE_REASONING_PROFILE",
    "medium",
).strip()
TAGGING_GEMINI_REASONING_PROFILE = os.getenv(
    "TAGGING_GEMINI_REASONING_PROFILE",
    "medium",
).strip()


def _load_request_options(environment_name: str) -> dict[str, Any]:
    """读取单个 Provider 的明确兼容参数，配置非法时安全回退为空对象。"""

    raw_value = os.getenv(environment_name, "").strip()
    if not raw_value:
        return {}

    try:
        parsed_value = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}

    return parsed_value if isinstance(parsed_value, dict) else {}


# 仅在已确认目标 model_id 支持时，才通过环境变量传入例如 reasoning 或
# temperature 等 Provider 专属请求字段；默认不盲目发送任何这类字段。
TAGGING_GPT_REQUEST_OPTIONS = _load_request_options(
    "TAGGING_GPT_REQUEST_OPTIONS_JSON"
)
TAGGING_CLAUDE_REQUEST_OPTIONS = _load_request_options(
    "TAGGING_CLAUDE_REQUEST_OPTIONS_JSON"
)
TAGGING_GEMINI_REQUEST_OPTIONS = _load_request_options(
    "TAGGING_GEMINI_REQUEST_OPTIONS_JSON"
)

# 打标调用的批量与限流约束。真实 B0DSHYXD4G / 202608 逐家串行验证中，20 条
# 输入可让 Claude 与 Gemini 以完整 JSON 通过严格校验；25 条的 Gemini 输出已
# 出现非法 JSON。因此以 20 作为当前目标批大小，发送前仍按实际 Prompt 大小
# 与输出容量自动拆分。GPT 因上游余额不足未能完成同批验证，额度恢复后应先进行
# 当前业务指定每个 Provider 的目标请求批次为 50 条；各 Provider 仍会在
# 请求层根据响应容量限制自动拆分，不能把余额错误误判为容量错误。
# 三家 Provider 分别受自己的 Semaphore 保护；三方所有 HTTP 请求（含重试）
# 同时受一个 UniAPI 账户级 RPM limiter 约束。
TAGGING_BATCH_SIZE = 50
TAGGING_GPT_CONCURRENCY = 8
TAGGING_CLAUDE_CONCURRENCY = 8
TAGGING_GEMINI_CONCURRENCY = 8
UNIAPI_RPM_LIMIT = 540

# 20 条是目标批次而非强制值。发送前根据实际序列化 Prompt 的 UTF-8 大小
# 与保守 token 估算自动二分，避免长 ProductContext 触发上下文或实体大小限制。
TAGGING_PROMPT_MAX_SERIALIZED_BYTES = 120_000
TAGGING_PROMPT_MAX_ESTIMATED_TOKENS = 30_000
# 若某个 Provider 显式配置 max_tokens / max_completion_tokens，则按该每行预算
# 评估并自动缩批；没有显式上限时不盲目注入不兼容的 Provider 参数。
TAGGING_RESPONSE_ESTIMATED_TOKENS_PER_ROW = 48
TAGGING_MAX_ATTEMPTS = 4
TAGGING_RETRY_BASE_DELAY_SECONDS = 1.0
TAGGING_RETRY_MAX_DELAY_SECONDS = 8.0
TAGGING_RETRY_JITTER_SECONDS = 0.25

# AI Tagging 是长生成请求，不能复用 SellerSprite 等普通 HTTP 请求的 10 秒
# 超时。模型生成期间不设本地读取时限；其余网络阶段仍显式限制。
TAGGING_HTTP_CONNECT_TIMEOUT_SECONDS = 10.0
TAGGING_HTTP_READ_TIMEOUT_SECONDS = None
TAGGING_HTTP_WRITE_TIMEOUT_SECONDS = 30.0
TAGGING_HTTP_POOL_TIMEOUT_SECONDS = 30.0

# 写入超时也可能意味着服务端已收到部分或全部请求，因此保持有限且延迟的
# 谨慎重试；连接和连接池超时则继续按通用 transient 重试处理。
TAGGING_WRITE_TIMEOUT_MAX_ATTEMPTS = 2
TAGGING_WRITE_TIMEOUT_RETRY_DELAY_SECONDS = 30.0

# 更新服务只使用内网发布源，不读取或上传任何业务数据、Cookie、数据库配置
# 或 AI 凭据。清单必须由离线 Ed25519 私钥签名，客户端只内置公钥。
UPDATE_MANIFEST_URL = os.getenv(
    "FLOW_ANALYSIS_UPDATE_MANIFEST_URL",
    "http://192.168.110.107:18083/flow-analysis/stable.json",
).strip()
UPDATE_CHANNEL = "stable"
UPDATE_ALLOW_INSECURE_HTTP = True
UPDATE_HTTP_TIMEOUT_SECONDS = 10.0
UPDATE_MANIFEST_MAX_BYTES = 256 * 1024
UPDATE_PACKAGE_MAX_BYTES = 2 * 1024 * 1024 * 1024
UPDATE_DOWNLOAD_DIR = APP_DATA_DIR / "updates"
UPDATE_UPDATER_EXECUTABLE_NAME = "FlowAnalysisUpdater.exe"

# 私钥绝不写入仓库、安装包或内网发布服务器；客户端只能以此公钥验证清单。
UPDATE_MANIFEST_PUBLIC_KEY_BASE64 = (
    "6D7GMHlR6Cavcmjr+UgRvLagqXTKY8hou1lukE3oObs="
)
