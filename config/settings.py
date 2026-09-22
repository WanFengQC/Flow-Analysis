import os
from pathlib import Path


# 程序名称
APP_NAME = "Flow Analysis"

# Cookie 获取接口
COOKIE_API_URL = "http://192.168.110.107:18765/api/cookie"

# HTTP 请求超时时间，单位：秒
REQUEST_TIMEOUT = 10.0

# SellerSprite API 客户端的统一服务地址。
# 具体业务接口路径由 ApiService 管理，不在配置层逐个定义。
SELLERSPRITE_BASE_URL = "https://www.sellersprite.com"

# Windows 本地应用数据目录
# 正常情况下最终路径类似：
# C:\Users\用户名\AppData\Local\Flow Analysis
APP_DATA_DIR = Path(
    os.getenv("LOCALAPPDATA", Path.home())
) / APP_NAME

# Cookie 本地缓存文件路径
COOKIE_CACHE_FILE = APP_DATA_DIR / "cookie.json"

# 用于验证 Cookie 是否有效的业务接口
SELLERSPRITE_ME_URL = "https://www.sellersprite.com/v2/me"
