"""Cookie 启动同步与本地缓存来源策略测试。"""

import unittest
from unittest.mock import Mock

from services.cookie_service import CookieService


class CookieServiceFreshnessTest(unittest.TestCase):
    """验证启动时必查内网服务，但 API 请求只使用本地缓存文件。"""

    def test_startup_fetches_remote_and_rewrites_only_when_changed(self) -> None:
        """远端 Cookie 不同时，写入后必须重新从本地文件读取返回。"""

        cached_cookie = {"session": "cached"}
        fresh_cookie = {"session": "fresh"}
        service = CookieService()
        service._load_cached_cookie = Mock(
            side_effect=[cached_cookie, fresh_cookie]
        )
        service._fetch_cookie = Mock(return_value=fresh_cookie)
        service._save_cookie = Mock()

        actual = service.get_cookie()

        self.assertEqual(actual, fresh_cookie)
        service._fetch_cookie.assert_called_once_with()
        service._save_cookie.assert_called_once_with(fresh_cookie)
        self.assertEqual(service._load_cached_cookie.call_count, 2)

    def test_startup_does_not_touch_file_when_remote_cookie_is_identical(self) -> None:
        """远端内容相同仍要请求检查，但不得改写本地文件。"""

        cached_cookie = {"session": "same"}
        service = CookieService()
        service._load_cached_cookie = Mock(return_value=cached_cookie)
        service._fetch_cookie = Mock(return_value=dict(cached_cookie))
        service._save_cookie = Mock()

        actual = service.get_cookie()

        self.assertEqual(actual, cached_cookie)
        service._fetch_cookie.assert_called_once_with()
        service._save_cookie.assert_not_called()
        self.assertEqual(service._load_cached_cookie.call_count, 1)

    def test_falls_back_to_local_file_only_when_cookie_service_is_unavailable(self) -> None:
        """内网服务失败时保留本地文件，不额外调用 /v2/me。"""

        cached_cookie = {"session": "cached"}
        service = CookieService()
        service._load_cached_cookie = Mock(return_value=cached_cookie)
        service._fetch_cookie = Mock(side_effect=RuntimeError("service unavailable"))
        service._save_cookie = Mock()

        actual = service.get_cookie()

        self.assertEqual(actual, cached_cookie)
        service._save_cookie.assert_not_called()

    def test_refuses_when_remote_changed_but_local_reread_fails(self) -> None:
        """新内容写入后必须能从本地重新读取，否则不能交给 API。"""

        service = CookieService()
        service._load_cached_cookie = Mock(
            side_effect=[None, None]
        )
        service._fetch_cookie = Mock(return_value={"session": "fresh"})
        service._save_cookie = Mock()

        with self.assertRaisesRegex(RuntimeError, "写入本地缓存后校验失败"):
            service.get_cookie()
        service._save_cookie.assert_called_once_with({"session": "fresh"})
