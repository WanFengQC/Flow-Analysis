"""验证独立更新器只允许一个实例执行安装流程。"""

from __future__ import annotations

import os
import unittest
from uuid import uuid4

from pathlib import Path

from infrastructure.update_launcher import (
    _acquire_update_process_lock,
    _installer_command,
)


@unittest.skipUnless(os.name == "nt", "更新器互斥锁仅在 Windows 发布目标验证")
class UpdateLauncherTest(unittest.TestCase):
    """使用真实 Windows 命名互斥锁验证并发更新器会被拒绝。"""

    def test_second_updater_cannot_hold_same_lock(self) -> None:
        """第二个更新器必须立即放弃，避免并发安装和重复重启。"""

        mutex_name = f"Local\\FlowAnalysis.AutoUpdate.tests.{uuid4().hex}"
        first_lock = _acquire_update_process_lock(mutex_name)
        self.assertIsNotNone(first_lock)
        try:
            self.assertIsNone(_acquire_update_process_lock(mutex_name))
        finally:
            assert first_lock is not None
            first_lock.release()

        third_lock = _acquire_update_process_lock(mutex_name)
        self.assertIsNotNone(third_lock)
        assert third_lock is not None
        third_lock.release()

    def test_installer_command_keeps_visible_progress_window(self) -> None:
        """更新安装必须显示 Inno Setup 进度，不能退回完全静默模式。"""

        command = _installer_command(Path("C:/updates/FlowAnalysisSetup.exe"))

        self.assertIn("/SILENT", command)
        self.assertNotIn("/VERYSILENT", command)
        self.assertIn("/NOCANCEL", command)


if __name__ == "__main__":
    unittest.main()
