import sys

from PySide6.QtWidgets import QApplication

from config.database_settings import DATABASE_SETTINGS
from controllers.main_controller import MainController
from infrastructure.application_runtime import ApplicationRuntime
from views.main_window import MainWindow


def main() -> int:
    """Flow Analysis 程序入口。"""

    # 创建 Qt 应用。
    # QApplication 必须在任何 QWidget 创建之前初始化。
    app = QApplication(sys.argv)

    # 创建整个程序共用的基础设施运行时。
    #
    # ApplicationRuntime 内部负责：
    # 1. asyncio 后台 EventLoop；
    # 2. PostgreSQL 异步连接池；
    # 3. 数据库基础设施生命周期。
    runtime = ApplicationRuntime(
        DATABASE_SETTINGS
    )

    # 创建主窗口。
    window = MainWindow()

    # 创建主控制器，并把 Runtime 注入 Controller。
    #
    # Controller 不自己创建数据库连接池，
    # 而是通过 ApplicationRuntime 使用基础设施。
    controller = MainController(
        window,
        runtime,
    )

    # 先显示主窗口。
    window.show()

    # 再启动后台初始化。
    #
    # 这里会同时开始：
    # - PostgreSQL 初始化
    # - Cookie 初始化
    #
    # 不会等待数据库连接完成后才显示窗口。
    controller.start()

    # Qt 即将退出时，统一关闭基础设施。
    #
    # ApplicationRuntime.shutdown() 内部顺序应当是：
    #
    # PostgreSQL Connection Pool
    #          ↓
    # asyncio EventLoop
    #
    # 不能反过来。
    app.aboutToQuit.connect(
        runtime.shutdown
    )

    # 启动 Qt 主事件循环。
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())