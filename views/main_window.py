from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QLabel,
)


class MainWindow(QMainWindow):
    """Flow Analysis 主窗口。"""

    def __init__(self):
        super().__init__()

        # 设置窗口标题和初始大小
        self.setWindowTitle("Flow Analysis")
        self.resize(1000, 700)

        # 创建主窗口的中央内容区域
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        # 创建垂直布局管理器
        # 后续主界面的控件会按照从上到下的顺序加入这里
        self.main_layout = QVBoxLayout()
        self.central_widget.setLayout(self.main_layout)

        # 显示程序当前运行状态
        # 例如：正在获取 Cookie、初始化完成、请求失败等
        self.status_label = QLabel("正在启动...")
        self.main_layout.addWidget(self.status_label)

    def set_status(self, message: str):
        """修改主界面显示的程序状态。"""

        self.status_label.setText(message)