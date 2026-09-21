from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QLabel,
    QPushButton,
)


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        # 设置标题&窗口大小
        self.setWindowTitle("数据分析工具")
        self.resize(1000, 700)

        # 创建中心画布
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)

        # 创建垂直布局管理器
        self.main_layout = QVBoxLayout()
        self.central_widget.setLayout(self.main_layout)

        # 设置状态标签
        self.status_label = QLabel("等待操作")
        self.main_layout.addWidget(self.status_label)

        # 设置测试按钮
        self.test_button = QPushButton("测试")
        self.main_layout.addWidget(self.test_button)

        # 设置测试按钮触发信号-点击
        self.test_button.clicked.connect(self.on_test_clicked)

    def on_test_clicked(self):
        # 修改按钮文本
        self.status_label.setText("按钮被点击了")