import json
from collections.abc import Mapping
from typing import Any, Literal

import httpx

from config.settings import (
    REQUEST_TIMEOUT,
    SELLERSPRITE_BASE_URL,
)


class ApiServiceError(RuntimeError):
    """SellerSprite API 客户端的基础异常。"""


class SellerSpriteHttpError(ApiServiceError):
    """SellerSprite 返回了非成功 HTTP 状态。"""


class SellerSpriteTransientError(ApiServiceError):
    """SellerSprite 的暂时性网络或服务端错误，可由业务层决定重试。"""


class SellerSpriteAuthenticationError(ApiServiceError):
    """SellerSprite 认证失效或要求重新登录。"""


class SellerSpriteResponseError(ApiServiceError):
    """SellerSprite 响应无法通过通用结构校验。"""


class SellerSpriteBusinessError(ApiServiceError):
    """SellerSprite 明确返回了业务失败，通常不适合机械重试。"""


class SellerSpriteDataSourcePermissionError(SellerSpriteBusinessError):
    """当前会话无权访问 relation 数据源，需要刷新 Cookie 后再判定。"""


class ApiService:
    """SellerSprite 的统一 HTTP API 客户端。"""

    # 当前只设置已确认需要的最小公共请求头。
    # Cookie 必须由 httpx.AsyncClient(cookies=...) 管理，
    # 不能手工拼接 Cookie Header。
    _DEFAULT_HEADERS = {
        "Accept": "*/*",
        "User-Agent": "Apifox/1.0.0 (https://apifox.com)",
    }

    # 以下值用于识别明确表达“未认证”的通用响应码。
    # 未知的非 OK code 仍保留为普通业务响应异常，避免错误误判。
    _AUTHENTICATION_ERROR_CODES = frozenset(
        {
            "UNAUTHORIZED",
            "UNAUTHENTICATED",
            "AUTHENTICATION_FAILED",
            "LOGIN_REQUIRED",
            "NOT_LOGIN",
        }
    )
    _DATA_SOURCE_PERMISSION_MESSAGE_FRAGMENTS = frozenset(
        {
            "没有权限访问该数据源",
            "无权限访问该数据源",
            "no permission to access this data source",
        }
    )

    def __init__(self, cookies: dict[str, str]):
        """使用当前有效 Cookie 创建可长期复用的异步 HTTP Client。"""

        # Client 由 ApiService 长期持有，后续业务方法共享连接池、Cookie、
        # base_url、超时和默认 Header，避免为每个接口重复创建客户端。
        #
        # 网络请求必须由调用方提交到现有 AsyncRuntime 的 EventLoop 中执行。
        # 此处只创建 Client，不在 Qt 主线程发起任何网络 I/O。
        self.client = httpx.AsyncClient(
            base_url=SELLERSPRITE_BASE_URL,
            cookies=cookies,
            timeout=REQUEST_TIMEOUT,
            headers=self._DEFAULT_HEADERS,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        """在 AsyncRuntime 的 EventLoop 中关闭 HTTP Client。"""

        await self.client.aclose()

    async def replace_cookies(self, cookies: dict[str, str]) -> None:
        """在同一事件循环内原子切换到刷新后的 Cookie Client。"""

        # relation 数据源权限刷新后，旧 Client 仍持有过期 Cookie。必须先创建
        # 新 Client 再关闭旧连接池，保证后续重试只会使用新 Cookie。
        previous_client = self.client
        self.client = httpx.AsyncClient(
            base_url=SELLERSPRITE_BASE_URL,
            cookies=cookies,
            timeout=REQUEST_TIMEOUT,
            headers=self._DEFAULT_HEADERS,
            follow_redirects=False,
        )
        await previous_client.aclose()

    async def get_relation_sources(
        self,
        keyword_or_asin: str,
        market: str,
        page_no: int,
        page_size: int,
        order: int,
        desc: bool,
        month: str,
    ) -> Any:
        """获取指定关键词或 ASIN 的流量来源数据。"""

        if not isinstance(desc, bool):
            raise TypeError("desc 必须为 bool")

        # SellerSprite 要求使用小写字符串 true / false。
        # 不依赖 httpx 对 Python bool 的默认序列化行为。
        desc_value = "true" if desc else "false"

        return await self._request(
            "GET",
            "/v3/api/relation/ta/source",
            params={
                "keywordOrAsin": keyword_or_asin,
                "market": market,
                "pageNo": page_no,
                "pageSize": page_size,
                "order": order,
                "desc": desc_value,
                "month": month,
            },
        )

    async def prewarm_relation_monthly(
        self,
        asin: str,
        month: str,
        station: str = "COM",
    ) -> Any:
        """触发指定 ASIN 与月份的 relation 月度数据预热。"""

        return await self._request(
            "GET",
            "/v3/api/relation/ta/monthly",
            params={
                "asin": asin,
                "station": station,
                "month": month,
            },
        )

    async def get_relation_reversing(
        self,
        asin: str,
        month: str,
        market: str = "COM",
        limit: int = 100,
        skip: int = 0,
    ) -> Any:
        """获取指定 ASIN 与月份的 relation reversing 单次结果。"""

        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit <= 0
        ):
            raise ValueError("limit 必须大于 0")

        if (
            isinstance(skip, bool)
            or not isinstance(skip, int)
            or skip < 0
        ):
            raise ValueError("skip 必须为非负整数")

        # 已确认的固定筛选条件集中在该业务方法中，调用方只提供实际会
        # 变化的 ASIN、月份与分页参数，避免 Controller 重复拼接请求体。
        return await self._request(
            "POST",
            "/v3/api/relation/reversing",
            params={"market": market},
            json_body={
                "asin": asin,
                "limit": limit,
                "skip": skip,
                "month": month,
                "badges": [],
                "conversionKeywordTypes": [],
                "trafficKeywordTypes": [],
                "order": 12,
                "desc": True,
                "exactly": False,
                "ac": False,
                "keywordBidMatchType": "exact",
                "filterDeletedKeywords": False,
            },
        )

    async def _request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> Any:
        """发送请求并统一校验 SellerSprite 的通用响应 envelope。"""

        # 当前公共底层能力只预留已确认的 GET、POST 请求方法。
        if method not in {"GET", "POST"}:
            raise ValueError(f"不支持的 SellerSprite 请求方法：{method}")

        try:
            response = await self.client.request(
                method=method,
                url=path,
                params=params,
                json=json_body,
            )
        except httpx.HTTPError as exc:
            # 网络、超时和协议错误统一转换为客户端异常，
            # 但不在异常文本中写入 Cookie 等认证信息。
            raise SellerSpriteTransientError(
                "SellerSprite 请求失败"
            ) from exc

        self._raise_for_authentication_failure(response)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if response.status_code >= 500:
                raise SellerSpriteTransientError(
                    "SellerSprite 服务暂时不可用"
                ) from exc

            raise SellerSpriteHttpError(
                f"SellerSprite HTTP 状态异常：{response.status_code}"
            ) from exc

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise SellerSpriteResponseError(
                "SellerSprite 响应不是有效 JSON"
            ) from exc

        if not isinstance(payload, dict):
            raise SellerSpriteResponseError(
                "SellerSprite 响应不是预期的 JSON 对象"
            )

        if "code" not in payload:
            raise SellerSpriteResponseError(
                "SellerSprite 响应缺少 code"
            )

        code = payload["code"]
        if code != "OK":
            message = payload.get("message", "未提供错误说明")

            if self._is_authentication_error_code(code):
                raise SellerSpriteAuthenticationError(
                    f"SellerSprite 认证失效：{message}"
                )

            if self._is_data_source_permission_error(message):
                raise SellerSpriteDataSourcePermissionError(
                    f"SellerSprite 数据源无权限：{message}"
                )

            raise SellerSpriteBusinessError(
                f"SellerSprite 接口返回错误：{message}"
            )

        if "data" not in payload:
            raise SellerSpriteResponseError(
                "SellerSprite 成功响应缺少 data"
            )

        # data 的内部结构由具体业务方法或其调用方按真实接口处理，
        # 公共请求层不预设分页、列表或其他业务字段。
        return payload["data"]

    @classmethod
    def _is_authentication_error_code(cls, code: Any) -> bool:
        """判断 SellerSprite 通用响应码是否明确表示认证失效。"""

        return (
            isinstance(code, str)
            and code.upper() in cls._AUTHENTICATION_ERROR_CODES
        )

    @classmethod
    def _is_data_source_permission_error(cls, message: Any) -> bool:
        """识别已确认的 relation 数据源权限拒绝业务消息。"""

        if not isinstance(message, str):
            return False
        normalized_message = message.strip().casefold()
        return any(
            fragment.casefold() in normalized_message
            for fragment in cls._DATA_SOURCE_PERMISSION_MESSAGE_FRAGMENTS
        )

    @staticmethod
    def _raise_for_authentication_failure(
        response: httpx.Response,
    ) -> None:
        """识别 HTTP 层明确的认证失效和跳转登录场景。"""

        if response.status_code in (401, 403):
            raise SellerSpriteAuthenticationError(
                f"SellerSprite 认证失效，HTTP 状态：{response.status_code}"
            )

        location = response.headers.get("location", "")
        if (
            response.status_code in (301, 302, 303, 307, 308)
            and "login" in location.lower()
        ):
            raise SellerSpriteAuthenticationError(
                "SellerSprite 要求重新登录"
            )
