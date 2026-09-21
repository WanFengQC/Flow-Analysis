from PySide6.QtCore import QObject, Signal, Slot

from services.cookie_service import CookieService


class CookieWorker(QObject):
    """在后台线程中获取 Cookie。"""

    # Cookie 获取成功时发送 Cookie 字典
    cookie_ready = Signal(object)

    # Cookie 获取失败时发送错误信息
    error = Signal(str)

    # 无论成功还是失败，任务结束时都会发送
    finished = Signal()

    @Slot()
    def run(self):
        """执行 Cookie 获取任务。"""

        try:
            # 调用 CookieService 获取可用 Cookie
            cookie_service = CookieService()
            cookie = cookie_service.get_cookie()

            # 将获取到的 Cookie 发送给主线程
            self.cookie_ready.emit(cookie)

        except Exception as exc:
            # 将异常转换成字符串后发送给主线程
            self.error.emit(str(exc))

        finally:
            # 无论任务成功还是失败，都通知主线程任务已经结束
            self.finished.emit()