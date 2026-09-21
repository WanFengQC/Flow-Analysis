from PySide6.QtCore import QObject, QThread, Slot

from services.api_service import ApiService
from workers.cookie_worker import CookieWorker


class MainController(QObject):
    """
    Flow Analysis 主控制器。

    主要负责：
    1. 协调主窗口和后台任务
    2. 启动 Cookie 获取任务
    3. 接收 Cookie 获取结果
    4. 创建和维护 ApiService
    """

    def __init__(self, window):
        super().__init__()

        # 保存主窗口对象
        # Controller 后面通过它更新界面状态
        self.window = window

        # 当前正在使用的 Cookie
        # 程序刚启动时还没有 Cookie，所以初始值为 None
        self.cookie = None

        # 业务 API 服务
        # Cookie 获取成功后才会创建
        self.api_service = None

        # Cookie 获取任务所使用的后台线程
        self.cookie_thread = None

        # 真正负责执行 Cookie 获取逻辑的 Worker
        self.cookie_worker = None

    def start(self):
        """
        启动 Flow Analysis 的初始化流程。

        当前程序启动后第一件事：
        自动获取一个可用 Cookie。
        """

        self._start_cookie_task()

    def _start_cookie_task(self):
        """在后台线程中启动 Cookie 获取任务。"""

        # 如果 Cookie 线程已经存在，
        # 说明任务正在执行，避免重复启动。
        if self.cookie_thread is not None:
            return

        # 告诉用户当前正在执行什么任务
        self.window.set_status("正在获取 Cookie...")

        # 创建一个新的后台线程
        self.cookie_thread = QThread()

        # 创建 Cookie Worker
        self.cookie_worker = CookieWorker()

        # 将 Worker 移动到后台线程
        # 之后 Worker 的 run() 会在这个线程中执行
        self.cookie_worker.moveToThread(
            self.cookie_thread
        )

        # -------------------------
        # 连接线程和 Worker 的信号
        # -------------------------

        # 后台线程启动后，执行 Worker.run()
        self.cookie_thread.started.connect(
            self.cookie_worker.run
        )

        # Cookie 获取成功后，
        # 执行 Controller 的 _on_cookie_ready()
        self.cookie_worker.cookie_ready.connect(
            self._on_cookie_ready
        )

        # Cookie 获取失败后，
        # 执行 Controller 的 _on_cookie_error()
        self.cookie_worker.error.connect(
            self._on_cookie_error
        )

        # Worker 工作结束后，
        # 告诉线程退出
        self.cookie_worker.finished.connect(
            self.cookie_thread.quit
        )

        # Worker 工作完成后，
        # 让 Qt 在合适的时机清理 Worker
        self.cookie_worker.finished.connect(
            self.cookie_worker.deleteLater
        )

        # 线程真正结束后，
        # 让 Qt 清理 QThread 对象
        self.cookie_thread.finished.connect(
            self.cookie_thread.deleteLater
        )

        # 线程结束后，
        # 清空 Controller 中保存的引用
        self.cookie_thread.finished.connect(
            self._on_cookie_thread_finished
        )

        # 正式启动后台线程
        self.cookie_thread.start()

    @Slot(object)
    def _on_cookie_ready(self, cookie):
        """处理 Cookie 获取成功。"""

        # 保存当前可用 Cookie
        self.cookie = cookie

        # 如果之前已经存在 ApiService，
        # 先关闭旧的 HTTP Client。
        if self.api_service is not None:
            self.api_service.close()

        # 使用最新 Cookie 创建业务 API 服务
        self.api_service = ApiService(cookie)

        # Cookie 和 API Client 都准备完成
        self.window.set_cookie_status("正常")
        self.window.set_api_status("就绪")
        self.window.set_status("初始化完成")

    @Slot(str)
    def _on_cookie_error(self, message):
        """处理 Cookie 获取失败。"""

        # 获取失败时，不保留无效 Cookie
        self.cookie = None

        # 明确保留永久状态栏中的失败状态，避免仅临时消息消失后无从判断。
        self.window.set_cookie_status("获取失败")
        self.window.set_api_status("未初始化")

        # 把具体错误显示到界面
        self.window.set_status(
            f"Cookie 获取失败：{message}"
        )

    @Slot()
    def _on_cookie_thread_finished(self):
        """Cookie 后台线程结束后的清理工作。"""

        # Qt 对象已经通过 deleteLater() 安排清理，
        # 这里再清除 Python 中保存的引用。
        self.cookie_worker = None
        self.cookie_thread = None
