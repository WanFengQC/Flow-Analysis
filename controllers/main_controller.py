from concurrent.futures import Future

from PySide6.QtCore import QObject, QThread, Signal, Slot

from infrastructure.application_runtime import ApplicationRuntime
from services.api_service import ApiService
from workers.cookie_worker import CookieWorker


class MainController(QObject):
    """
    Flow Analysis 主控制器。

    主要负责：
    1. 协调主窗口和后台任务；
    2. 启动 PostgreSQL 基础设施；
    3. 启动 Cookie 获取任务；
    4. 接收各后台任务结果；
    5. 创建和维护 ApiService。
    """

    # PostgreSQL 初始化成功。
    #
    # ApplicationRuntime 的 Future 回调不一定运行在 Qt 主线程，
    # 因此通过 Qt Signal 将结果安全传回主线程。
    database_ready = Signal()

    # PostgreSQL 初始化失败，并携带错误信息。
    database_failed = Signal(str)

    def __init__(
        self,
        window,
        runtime: ApplicationRuntime,
    ):
        super().__init__()

        # 保存主窗口 View。
        self.window = window

        # 保存应用基础设施运行时。
        # PostgreSQL 异步连接池由该对象统一管理。
        self.runtime = runtime

        # 当前正在使用的 Cookie。
        self.cookie = None

        # Cookie 成功后创建的业务 API 服务。
        self.api_service = None

        # Cookie 获取任务对应的 Qt 后台线程。
        self.cookie_thread = None

        # 真正执行 Cookie 获取工作的 Worker。
        self.cookie_worker = None

        # 记录两个核心初始化模块是否已经准备完成。
        #
        # 只有数据库和业务 API 都成功以后，
        # 才认为整个程序初始化完成。
        self._database_ready = False
        self._api_ready = False

        # 数据库后台任务完成后，通过 Signal 回到 Qt 主线程。
        self.database_ready.connect(
            self._on_database_ready
        )

        self.database_failed.connect(
            self._on_database_failed
        )

    def start(self):
        """
        启动 Flow Analysis 初始化流程。

        PostgreSQL 和 Cookie 相互独立，
        因此同时启动，不需要串行等待。
        """

        # 启动 PostgreSQL 异步基础设施。
        self._start_database()

        # 启动 Cookie 获取任务。
        self._start_cookie_task()

    def _start_database(self):
        """启动 PostgreSQL 数据库基础设施。"""

        # 立即更新永久状态栏。
        self.window.set_database_status("连接中")

        try:
            # ApplicationRuntime.start() 不会等待 PostgreSQL 完成，
            # 而是返回一个 Future。
            future = self.runtime.start()

            # PostgreSQL 初始化真正完成后，
            # 再进入结果处理函数。
            future.add_done_callback(
                self._on_database_future_done
            )

        except Exception as exc:
            # 这里主要处理 Runtime 自身连启动任务都无法提交的情况。
            self.database_failed.emit(str(exc))

    def _on_database_future_done(
        self,
        future: Future,
    ):
        """
        PostgreSQL 初始化 Future 完成后的回调。

        此函数可能运行在 asyncio 后台线程，
        所以禁止在这里直接操作 QWidget。
        """

        try:
            # add_done_callback() 只会在 Future 已完成后执行，
            # 因此这里 result() 不会再等待数据库。
            future.result()

        except Exception as exc:
            self.database_failed.emit(str(exc))
            return

        self.database_ready.emit()

    @Slot()
    def _on_database_ready(self):
        """处理 PostgreSQL 初始化成功。"""

        self._database_ready = True

        self.window.set_database_status("正常")

        # 检查整个程序是否已经完成初始化。
        self._update_initialization_status()

    @Slot(str)
    def _on_database_failed(
        self,
        message: str,
    ):
        """处理 PostgreSQL 初始化失败。"""

        self._database_ready = False

        self.window.set_database_status(
            "连接失败"
        )

        self.window.set_status(
            f"PostgreSQL 连接失败：{message}"
        )

    def _start_cookie_task(self):
        """在后台线程中启动 Cookie 获取任务。"""

        # 如果 Cookie 线程已经存在，
        # 说明任务正在执行，避免重复启动。
        if self.cookie_thread is not None:
            return

        # 告诉用户当前正在执行什么任务。
        self.window.set_status(
            "正在获取 Cookie..."
        )

        # 创建新的 Qt 后台线程。
        self.cookie_thread = QThread()

        # 创建 Cookie Worker。
        self.cookie_worker = CookieWorker()

        # 将 Worker 移到后台线程。
        self.cookie_worker.moveToThread(
            self.cookie_thread
        )

        # QThread 启动后执行 Worker.run()。
        self.cookie_thread.started.connect(
            self.cookie_worker.run
        )

        # Cookie 获取成功。
        self.cookie_worker.cookie_ready.connect(
            self._on_cookie_ready
        )

        # Cookie 获取失败。
        self.cookie_worker.error.connect(
            self._on_cookie_error
        )

        # Worker 完成后退出线程。
        self.cookie_worker.finished.connect(
            self.cookie_thread.quit
        )

        # 安排 Qt 清理 Worker。
        self.cookie_worker.finished.connect(
            self.cookie_worker.deleteLater
        )

        # 安排 Qt 清理 QThread。
        self.cookie_thread.finished.connect(
            self.cookie_thread.deleteLater
        )

        # 清除 Controller 中保存的 Python 引用。
        self.cookie_thread.finished.connect(
            self._on_cookie_thread_finished
        )

        # 正式启动线程。
        self.cookie_thread.start()

    @Slot(object)
    def _on_cookie_ready(
        self,
        cookie,
    ):
        """处理 Cookie 获取成功。"""

        # 保存当前可用 Cookie。
        self.cookie = cookie

        # 如果已经存在旧的 ApiService，
        # 创建新客户端前先释放旧 HTTP Client。
        if self.api_service is not None:
            self.api_service.close()

        # 使用最新 Cookie 创建业务 API 服务。
        self.api_service = ApiService(cookie)

        # API 初始化成功。
        self._api_ready = True

        self.window.set_cookie_status("正常")
        self.window.set_api_status("就绪")

        # 不直接显示“初始化完成”，
        # 因为此时 PostgreSQL 可能仍然正在连接。
        self._update_initialization_status()

    @Slot(str)
    def _on_cookie_error(
        self,
        message,
    ):
        """处理 Cookie 获取失败。"""

        # 获取失败时不保存无效 Cookie。
        self.cookie = None
        self._api_ready = False

        self.window.set_cookie_status(
            "获取失败"
        )

        self.window.set_api_status(
            "未初始化"
        )

        self.window.set_status(
            f"Cookie 获取失败：{message}"
        )

    def _update_initialization_status(self):
        """
        根据各基础模块状态决定是否完成初始化。

        防止 Cookie 初始化完成时数据库仍未完成，
        却错误显示“初始化完成”。
        """

        if (
            self._database_ready
            and self._api_ready
        ):
            self.window.set_status(
                "初始化完成"
            )

    @Slot()
    def _on_cookie_thread_finished(self):
        """Cookie 后台线程结束后的清理工作。"""

        self.cookie_worker = None
        self.cookie_thread = None