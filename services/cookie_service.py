import json

import httpx

from config.settings import (
    COOKIE_API_URL,
    COOKIE_CACHE_FILE,
    REQUEST_TIMEOUT,
)


class CookieService:
    """负责 Cookie 的读取、校验、获取和本地缓存。"""

    def get_cookie(self):
        """
        启动时同步最新 Cookie，但始终从本地缓存文件返回请求凭据。

        每次启动都会请求内网 Cookie 服务。若新旧内容相同，绝不改写本地文件；
        若内容不同，才覆盖本地缓存，随后重新读取该文件作为 API Client 的
        唯一 Cookie 来源。内网服务暂时不可用时，才回退已有本地文件。
        """

        cached_cookie = self._load_cached_cookie()

        try:
            return self._fetch_and_sync_local_cookie(cached_cookie)
        except (httpx.HTTPError, RuntimeError, json.JSONDecodeError):
            # 内网 Cookie 服务不可用时不修改缓存；若本地已有完整 Cookie，
            # 允许保留原行为继续运行，实际数据接口会继续负责权限判定。
            if cached_cookie is not None:
                return cached_cookie
            raise

    def refresh_cookie(self) -> dict[str, str]:
        """强制同步内网最新 Cookie，并返回重新读取后的本地副本。"""

        return self._fetch_and_sync_local_cookie(
            self._load_cached_cookie()
        )

    def _fetch_and_sync_local_cookie(
        self,
        cached_cookie: dict[str, str] | None,
    ) -> dict[str, str]:
        """取远端 Cookie，仅在变化时写本地，最后从本地文件读取返回。"""

        fresh_cookie = self._fetch_cookie()
        if cached_cookie == fresh_cookie:
            # 接口请求继续使用本地文件所代表的同一份 Cookie；相同内容不触碰
            # 文件，保留原有修改时间，避免无意义写入。
            return cached_cookie

        self._save_cookie(fresh_cookie)
        persisted_cookie = self._load_cached_cookie()
        if persisted_cookie == fresh_cookie:
            return persisted_cookie

        raise RuntimeError("最新 Cookie 写入本地缓存后校验失败")

    def _load_cached_cookie(self):
        """从本地缓存文件读取 Cookie。"""

        # 缓存文件不存在时，直接返回 None
        if not COOKIE_CACHE_FILE.exists():
            return None

        try:
            # 读取 cookie.json
            with COOKIE_CACHE_FILE.open(
                "r",
                encoding="utf-8",
            ) as file:
                cookie = json.load(file)

            # 缓存内容应该是一个字典
            # 如果文件内容结构异常，则视为无效缓存
            if not isinstance(cookie, dict):
                return None

            return cookie

        except (OSError, json.JSONDecodeError):
            # 文件读取失败或 JSON 损坏时，
            # 不影响程序启动，后续重新获取 Cookie
            return None

    def _save_cookie(self, cookie):
        """将 Cookie 字典保存到本地。"""
        # TODO: 补充 Cookie 加密！！！
        # 确保 Flow Analysis 的本地数据目录存在
        COOKIE_CACHE_FILE.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        # 将 Cookie 字典保存为 JSON 文件
        with COOKIE_CACHE_FILE.open(
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                cookie,
                file,
                ensure_ascii=False,
                indent=4,
            )

    def _fetch_cookie(self):
        """通过局域网接口获取新的 Cookie。"""

        # 请求 Cookie 接口
        response = httpx.get(
            COOKIE_API_URL,
            timeout=REQUEST_TIMEOUT,
        )

        # HTTP 层出现 4xx / 5xx 时直接抛出异常
        response.raise_for_status()

        # 将接口 JSON 响应转换成 Python 字典
        data = response.json()

        # 接口自己的业务状态码必须为 0
        if data.get("code") != 0:
            raise RuntimeError(
                f"Cookie 接口返回错误：{data.get('message', '未知错误')}"
            )

        # 接口判断当前获取到的 Cookie 必须有效
        if data.get("is_valid") is not True:
            raise RuntimeError("Cookie 接口返回了无效 Cookie")

        # 从完整响应中取出真正需要的 Cookie 字典
        cookies = data.get("cookies")

        # 防止接口异常返回 None、字符串或其他错误结构
        if not isinstance(cookies, dict) or not cookies:
            raise RuntimeError("Cookie 接口未返回有效的 cookies 数据")

        return cookies

    # TODO: 补充 Cookie 获取失败后的重试机制
