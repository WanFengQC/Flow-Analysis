import sys

from PySide6.QtWidgets import QApplication

from controllers.main_controller import MainController
from views.main_window import MainWindow


def main():
    """Flow Analysis 程序入口。"""

    # 创建 Qt 应用程序
    app = QApplication(sys.argv)

    # 创建主窗口
    window = MainWindow()

    # 创建主控制器
    controller = MainController(window)

    # 显示主窗口
    window.show()

    # 启动程序初始化流程
    controller.start()

    # 启动 Qt 事件循环
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())