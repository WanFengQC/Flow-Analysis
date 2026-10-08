import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from services.api_service import (
    ApiService,
    SellerSpriteDataSourcePermissionError,
    SellerSpriteResponseError,
    SellerSpriteTransientError,
)


# 分页进度依次为：当前页、总页数、已获取数量、总记录数。
# relation/source 接口的总页数元数据不可靠，因此当前总页数为 None。
PaginationProgressCallback = Callable[[int, int | None, int, int], None]
CookieRefreshCallback = Callable[[], Awaitable[None]]


class EmptyReversingDataError(SellerSpriteResponseError):
    """reversing 返回空数据或 total/items 不一致时抛出的可重试异常。"""


class ReversingPreparationRetriesExhaustedError(RuntimeError):
    """单个 ASIN 在所有预热与 reversing 尝试均失败后抛出的异常。"""

    def __init__(
        self,
        asin: str,
        attempts: int,
        last_reason: str,
    ) -> None:
        """保留供 Controller 展示人工重试弹窗所需的最小上下文。"""

        self.asin = asin
        self.attempts = attempts
        self.last_reason = last_reason
        super().__init__(
            "ASIN "
            f"{asin} 在 {attempts} 次“预热后请求”后仍无有效 reversing 数据："
            f"{last_reason}"
        )


class ReversingDataSourcePermissionError(RuntimeError):
    """刷新一次最新 Cookie 后仍被 relation 数据源拒绝。"""

    def __init__(self, asin: str, reason: str) -> None:
        self.asin = asin
        self.reason = reason
        super().__init__(
            f"ASIN {asin} 刷新 Cookie 后仍无法访问 relation 数据源：{reason}"
        )


class _RetryableReversingPreparationError(RuntimeError):
    """标记单轮预热或 reversing 中可以完整重新执行的临时失败。"""


class CrawlerService:
    """负责 SellerSprite relation 数据的分页与预处理业务编排。"""

    # 仅用于避免服务端持续返回完整页而导致无限请求。
    # 正常分页必须依据接口返回的 items 与 total，不以此作为业务上限。
    _MAX_RELATION_SOURCE_PAGES = 10_000
    _MAX_REVERSING_AUTO_RETRIES = 3
    _MAX_REVERSING_PAGES = 10_000
    # monthly 接口仅触发 SellerSprite 的异步数据准备。预热成功返回不等于
    # reversing 缓存已经可读，因此每轮预热后都必须给服务端短暂落地时间。
    _PREWARM_SETTLE_SECONDS = 1.0
    _RETRY_BACKOFF_SECONDS = 1.0

    def __init__(
        self,
        api_service: ApiService,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        refresh_cookie: CookieRefreshCallback | None = None,
    ) -> None:
        # ApiService 只执行单次 HTTP 请求；分页循环属于本 Service。
        self._api_service = api_service
        # 依赖注入让预热顺序与取消行为能够在不访问真实 SellerSprite 的
        # 单元测试中验证；默认 asyncio.sleep 支持正常 Task cancellation。
        self._sleep = sleep
        # 刷新由 Controller 注入，保证 Crawler 不直接触及 Qt 或 Cookie 文件。
        self._refresh_cookie = refresh_cookie

    async def get_all_relation_sources_for_asin_month(
        self,
        asin: str,
        market: str,
        month: str,
        page_size: int = 100,
        order: int = 1,
        desc: bool = True,
        progress_callback: PaginationProgressCallback | None = None,
    ) -> list[Any]:
        """获取指定 ASIN 在指定月份的全部 relation source 数据。"""

        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or page_size <= 0
        ):
            raise ValueError("page_size 必须大于 0")

        current_page = 1
        total_records: int | None = None
        all_items: list[Any] = []

        while True:
            if current_page > self._MAX_RELATION_SOURCE_PAGES:
                raise SellerSpriteResponseError(
                    "relation source 分页次数超过内部安全上限"
                )

            data = await self._api_service.get_relation_sources(
                keyword_or_asin=asin,
                market=market,
                page_no=current_page,
                page_size=page_size,
                order=order,
                desc=desc,
                month=month,
            )

            pager = self._extract_pager(
                data,
                has_previous_items=bool(all_items),
            )

            # 终止条件三：后续页面返回 pager=null，
            # 且之前已经取得过数据时，视为正常结束。
            if pager is None:
                break

            # 当前接口实测 page、pages、size、hasNextPage 均不可用。
            # 分页只能依据 items、total 和客户端维护的 pageNo。
            items = self._extract_items(pager)
            total = self._require_non_negative_int(pager, "total")

            if total_records is None:
                total_records = total
            elif total != total_records:
                raise SellerSpriteResponseError(
                    "relation source 各分页返回的总记录数不一致"
                )

            # 空页不能代表下一页仍然存在。若之前已取得过数据，
            # 将它作为接口返回结束信号；第一页空页仅允许 total 为 0。
            if not items:
                if all_items or total == 0:
                    break

                raise SellerSpriteResponseError(
                    "relation source 首页为空但总记录数大于 0"
                )

            all_items.extend(items)

            if progress_callback is not None:
                # 保留后续接入进度机制所需的四个数据点；
                # relation/source 暂无可信总页数，因此传入 None。
                # 本阶段不接入 Qt Signal，也不更新 UI。
                progress_callback(
                    current_page,
                    None,
                    len(all_items),
                    total,
                )

            # 终止条件一：累计记录数已经达到接口声明的总数。
            if len(all_items) >= total:
                break

            # 终止条件二：不足请求页大小，说明已经到达最后一页。
            if len(items) < page_size:
                break

            # 只有完整页且累计数量未达到 total 时，才继续请求下一页。
            current_page += 1

        if total_records is None:
            # 循环至少会请求第一页；此分支只用于保证内部状态完整。
            raise SellerSpriteResponseError(
                "relation source 未返回任何分页结果"
            )

        return all_items

    async def get_relation_reversing_for_asin_month(
        self,
        asin: str,
        month: str,
        market: str = "COM",
        limit: int = 100,
        skip: int = 0,
    ) -> Mapping[str, Any]:
        """预热后完整获取单个 ASIN 的 reversing 数据，失败时整轮重试。"""

        if skip != 0:
            raise ValueError(
                "完整 reversing 获取必须从 skip=0 开始"
            )

        total_attempts = self._MAX_REVERSING_AUTO_RETRIES + 1
        attempt = 1
        permission_refresh_used = False

        while attempt <= total_attempts:
            try:
                return await self._fetch_valid_reversing_once(
                    asin=asin,
                    month=month,
                    market=market,
                    limit=limit,
                    skip=skip,
                )
            except SellerSpriteDataSourcePermissionError as exc:
                # 此错误是权限拒绝，不是尚未预热或暂时网络波动。禁止携带
                # 同一 Cookie 盲目重试；只刷新一次 Cookie，再从预热开始。
                if permission_refresh_used or self._refresh_cookie is None:
                    raise ReversingDataSourcePermissionError(
                        asin,
                        str(exc),
                    ) from exc

                permission_refresh_used = True
                try:
                    await self._refresh_cookie()
                except Exception as refresh_exc:
                    raise ReversingDataSourcePermissionError(
                        asin,
                        f"刷新最新 Cookie 失败：{refresh_exc}",
                    ) from refresh_exc
                # 不消耗普通重试次数。下一轮仍会先执行 monthly 预热，
                # 然后等待就绪并请求 reversing。
                continue
            except _RetryableReversingPreparationError as exc:
                # 每次重试都必须从 monthly 预热重新开始，绝不能只重发
                # reversing POST；在下一轮预热前做可取消的短暂退避。
                if attempt == total_attempts:
                    raise ReversingPreparationRetriesExhaustedError(
                        asin,
                        total_attempts,
                        str(exc),
                    ) from exc
                await self._sleep(self._RETRY_BACKOFF_SECONDS)
                attempt += 1

        # 循环总会在成功时 return，或在最后一轮失败时抛出异常。
        raise RuntimeError("reversing 数据准备流程意外结束")

    async def _fetch_valid_reversing_once(
        self,
        *,
        asin: str,
        month: str,
        market: str,
        limit: int,
        skip: int,
    ) -> Mapping[str, Any]:
        """执行一轮“monthly 预热 → 等待就绪 → 完整 reversing 分页”。"""

        try:
            await self._api_service.prewarm_relation_monthly(
                asin=asin,
                station=market,
                month=month,
            )
        except (SellerSpriteTransientError, SellerSpriteResponseError) as exc:
            raise _RetryableReversingPreparationError(
                f"monthly 预热未返回可用响应：{exc}"
            ) from exc

        # 不使用阻塞 sleep：用户取消、启动新 generation 或应用退出时，
        # AsyncRuntime 会取消该 Task，等待和后续 HTTP 请求都会立即结束。
        await self._sleep(self._PREWARM_SETTLE_SECONDS)

        try:
            return await self._fetch_all_reversing_pages(
                asin=asin,
                month=month,
                market=market,
                limit=limit,
                skip=skip,
            )
        except (
            SellerSpriteTransientError,
            SellerSpriteResponseError,
        ) as exc:
            raise _RetryableReversingPreparationError(
                f"reversing 请求未返回可用响应：{exc}"
            ) from exc

    async def _fetch_all_reversing_pages(
        self,
        *,
        asin: str,
        month: str,
        market: str,
        limit: int,
        skip: int,
    ) -> Mapping[str, Any]:
        """从 skip=0 累计到服务端 total，并保留第一页所有其他顶层字段。"""

        first_page_data = await self._api_service.get_relation_reversing(
            asin=asin,
            month=month,
            market=market,
            limit=limit,
            skip=skip,
        )
        first_page = self._require_valid_reversing_data(first_page_data)
        expected_total = first_page["total"]
        all_items = list(first_page["items"])
        current_page_items = first_page["items"]
        page_count = 1

        if len(all_items) > expected_total:
            raise EmptyReversingDataError(
                "reversing 首页 items 数量超过 total"
            )

        while len(all_items) < expected_total:
            if page_count >= self._MAX_REVERSING_PAGES:
                raise EmptyReversingDataError(
                    "reversing 分页次数超过内部安全上限"
                )

            # 必须按当前页真实返回数量推进，不能假定每页都恰好等于 limit。
            skip += len(current_page_items)
            page_data = await self._api_service.get_relation_reversing(
                asin=asin,
                month=month,
                market=market,
                limit=limit,
                skip=skip,
            )
            current_page = self._require_valid_reversing_data(page_data)

            if current_page["total"] != expected_total:
                raise EmptyReversingDataError(
                    "reversing 各分页返回的 total 不一致"
                )

            # stats 当前没有可靠的跨页合并语义：结果始终保留第一页原值，
            # 后续页即使不同也不拼接、不覆盖，留待后续基于真实差异设计。

            current_page_items = current_page["items"]
            all_items.extend(current_page_items)
            page_count += 1

            if len(all_items) > expected_total:
                raise EmptyReversingDataError(
                    "reversing 累计 items 数量超过 total"
                )

        if len(all_items) != expected_total:
            raise EmptyReversingDataError(
                "reversing 累计 items 数量与 total 不一致"
            )

        # 只替换 items，站点、市场、stats 等第一页真实顶层字段均保留。
        merged_data = dict(first_page)
        merged_data["items"] = all_items
        return merged_data

    @staticmethod
    def _require_valid_reversing_data(
        data: Any,
    ) -> Mapping[str, Any]:
        """校验 reversing 成功响应中 total 与 items 的真实业务一致性。"""

        if not isinstance(data, Mapping):
            raise EmptyReversingDataError(
                "reversing 响应 data 不是对象"
            )

        if "items" not in data or "total" not in data:
            raise EmptyReversingDataError(
                "reversing 响应缺少 total 或 items"
            )

        items = data["items"]
        total = data["total"]
        if not isinstance(items, list):
            raise EmptyReversingDataError(
                "reversing 响应中的 items 不是列表"
            )

        if isinstance(total, bool) or not isinstance(total, int):
            raise EmptyReversingDataError(
                "reversing 响应中的 total 不是整数"
            )

        # 每一页都必须同时具有正 total 与非空 items；任一方为空或两者
        # 不一致，都不能误判为可进入后续分析的成功结果。
        if total <= 0 or not items:
            raise EmptyReversingDataError(
                "reversing 返回空数据"
            )

        return data

    @staticmethod
    def _extract_pager(
        data: Any,
        *,
        has_previous_items: bool,
    ) -> Mapping[str, Any] | None:
        """从单次接口 data 中取出并校验 pager 对象。"""

        if not isinstance(data, Mapping):
            raise SellerSpriteResponseError(
                "relation source 响应 data 类型无效"
            )

        pager = data.get("pager")

        if pager is None and has_previous_items:
            return None

        if not isinstance(pager, Mapping):
            raise SellerSpriteResponseError(
                "relation source 响应缺少有效 pager"
            )

        return pager

    @staticmethod
    def _extract_items(pager: Mapping[str, Any] | None) -> list[Any]:
        """读取当前页 items，并保留空列表作为正常结束信号。"""

        if pager is None:
            return []

        if "items" not in pager:
            raise SellerSpriteResponseError(
                "relation source 响应缺少 items"
            )

        items = pager["items"]
        if not isinstance(items, list):
            raise SellerSpriteResponseError(
                "relation source 响应中的 items 类型无效"
            )

        return items

    @staticmethod
    def _require_non_negative_int(
        pager: Mapping[str, Any],
        field_name: str,
    ) -> int:
        """读取必须大于等于零的分页整数。"""

        value = pager.get(field_name)

        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SellerSpriteResponseError(
                f"relation source 响应中的 {field_name} 必须为非负整数"
            )

        return value
