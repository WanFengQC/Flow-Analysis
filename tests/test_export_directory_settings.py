"""默认 Excel 导出目录的界面偏好持久化测试。"""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

# 测试环境不依赖真实桌面显示服务，必须在导入 Qt 前固定离屏平台。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from views.main_window import MainWindow


class ExportDirectorySettingsTest(unittest.TestCase):
    """验证首次目录选择只发生一次，后续自动导出复用相同目录。"""

    @classmethod
    def setUpClass(cls) -> None:
        """整个模块共用一个 QApplication。"""

        cls._application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        """每个测试使用独立临时 INI，绝不读写用户真实偏好。"""

        self._temporary_directory = TemporaryDirectory()
        self.window = MainWindow()
        settings_path = Path(self._temporary_directory.name) / "settings.ini"
        self.window._user_settings = QSettings(
            str(settings_path),
            QSettings.Format.IniFormat,
        )

    def tearDown(self) -> None:
        """销毁 View 与临时配置文件。"""

        self.window.close()
        self.window.deleteLater()
        self._application.processEvents()
        self._temporary_directory.cleanup()

    def test_selected_directory_is_reused_without_a_second_dialog(self) -> None:
        """首次选择后应直接返回已保存目录，不再显示文件夹选择框。"""

        export_directory = self._temporary_directory.name
        with patch(
            "views.main_window.QFileDialog.getExistingDirectory",
            return_value=export_directory,
        ) as choose_directory:
            self.assertEqual(
                self.window.ensure_export_directory(),
                export_directory,
            )

        self.assertEqual(choose_directory.call_count, 1)
        with patch(
            "views.main_window.QFileDialog.getExistingDirectory"
        ) as choose_directory:
            self.assertEqual(
                self.window.ensure_export_directory(),
                export_directory,
            )

        choose_directory.assert_not_called()
