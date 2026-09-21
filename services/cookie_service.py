import json
from datetime import datetime, timezone

import httpx

from config.settings import (
    COOKIE_API_URL,
    COOKIE_CACHE_FILE,
    REQUEST_TIMEOUT,
    SELLERSPRITE_ME_URL,
)


class CookieService:
    """负责 Cookie 的读取、校验、获取和本地缓存。"""

    def get_cookie(self):
        """
        获取一个当前可用的 Cookie。

        优先使用本地缓存；
        缓存失效后重新调用 Cookie 接口获取。
        """

        # 读取本地 Cookie
        cached_cookie = self._load_cached_cookie()

        if cached_cookie is not None:
            # 验证当前缓存 Cookie
            is_valid, detail, validated_at = self._is_cookie_valid(
                cached_cookie
            )

            if is_valid:
                return cached_cookie

        # 本地 Cookie 不存在或已经失效，
        # 重新从局域网 Cookie 接口获取
        cookie = self._fetch_cookie()

        # 保存新的 Cookie
        self._save_cookie(cookie)

        return cookie

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

    def _is_cookie_valid(
            self,
            cookies: dict[str, str],
    ) -> tuple[bool, str, datetime]:
        """
        验证 Cookie 是否仍然有效。

        返回：
            bool:
                Cookie 是否有效

            str:
                验证结果描述

            datetime:
                本次验证时间（UTC）
        """

        # 先清洗 Cookie，去掉空 key 和空 value
        safe_cookies = self._normalize_cookies(cookies)

        # Cookie 为空时，没有必要继续请求验证接口
        if not safe_cookies:
            return (
                False,
                "cookie empty",
                datetime.now(timezone.utc),
            )

        try:
            # 不自动跟随重定向。
            # 如果 Cookie 失效，服务器通常会把请求重定向到登录页面，
            # 我们需要自己看到这个 302，而不是让 httpx 自动跳过去。
            with httpx.Client(
                    timeout=REQUEST_TIMEOUT,
                    follow_redirects=False,
            ) as client:
                response = client.get(
                    SELLERSPRITE_ME_URL,
                    cookies=safe_cookies,
                )

            # 当前验证完成时间
            validated_at = datetime.now(timezone.utc)

            # HTTP 状态码
            status_code = response.status_code

            # 某些登录失效场景会通过 Location 告诉浏览器跳转登录页
            location = response.headers.get("location", "")

            # 正常访问业务接口，说明 Cookie 当前有效
            if status_code == 200:
                return True, "ok", validated_at

            # Cookie 失效后，接口可能重定向到登录页面
            if (
                    status_code in (301, 302, 303, 307, 308)
                    and "login" in location.lower()
            ):
                return False, "redirect login", validated_at

            # 有些接口不会重定向，而是直接返回未授权状态
            if status_code in (401, 403):
                return False, f"http {status_code}", validated_at

            # 其他非预期状态统一视为验证失败
            return False, f"http {status_code}", validated_at

        except httpx.HTTPError as exc:
            # 只捕获 HTTPX 的网络相关异常，
            # 避免把真正的代码 Bug 也误判成 Cookie 失效
            return (
                False,
                f"validate error: {exc}",
                datetime.now(timezone.utc),
            )

    @staticmethod
    def _normalize_cookies(
            cookies: dict[str, str] | None,
    ) -> dict[str, str]:
        """
        清洗 Cookie 数据。

        去掉：
        - 空 key
        - 空 value

        同时确保 key 和 value 都是字符串。
        """

        if not cookies:
            return {}

        safe_cookies = {}

        for key, value in cookies.items():
            key = str(key).strip()
            value = str(value).strip()

            if not key or not value:
                continue

            safe_cookies[key] = value

        return safe_cookies

    # TODO: 补充 Cookie 获取失败后的重试机制
