import httpx

from config.settings import REQUEST_TIMEOUT


class ApiService:
    """负责 Flow Analysis 的业务 API 请求。"""

    def __init__(self, cookies: dict[str, str]):
        # 创建一个可复用的 HTTP Client。
        # 后续所有业务接口请求都会自动携带这里传入的 Cookie。
        self.client = httpx.Client(
            cookies=cookies,
            timeout=REQUEST_TIMEOUT,
            follow_redirects=False,
        )

    def close(self):
        """关闭 HTTP Client，释放连接等网络资源。"""

        self.client.close()