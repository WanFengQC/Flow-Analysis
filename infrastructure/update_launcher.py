"""独立更新器：等待主程序退出，验证安装包，然后静默安装并重启。"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path


# 所有已安装版本共用同一把互斥锁，保证同一台 Windows 设备在任意时刻
# 只有一个 Flow Analysis 更新器能够等待、安装和重启应用。
_UPDATE_MUTEX_NAME = "Local\\FlowAnalysis.AutoUpdate"
_ERROR_ALREADY_EXISTS = 183


class _UpdateProcessLock:
    """Windows 命名互斥锁的轻量封装，生命周期严格覆盖整个更新过程。"""

    def __init__(self, handle: int) -> None:
        self._handle = handle

    def release(self) -> None:
        """关闭互斥锁句柄；重复释放不会影响安装器的退出路径。"""

        if self._handle:
            import ctypes

            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(
                self._handle
            )
            self._handle = 0


def _acquire_update_process_lock(
    mutex_name: str = _UPDATE_MUTEX_NAME,
) -> _UpdateProcessLock | None:
    """取得唯一更新器锁；已有更新器运行时返回 None 而不是并发安装。"""

    if os.name != "nt":
        # 发布目标是 Windows。非 Windows 的脚本调用保留可预测行为，避免
        # 本地静态检查或导入时依赖 Windows API。
        return _UpdateProcessLock(0)

    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_bool,
        ctypes.c_wchar_p,
    ]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, mutex_name)
    if not handle:
        raise OSError("无法创建自动更新互斥锁")
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return _UpdateProcessLock(handle)


def _calculate_sha256(path: Path) -> str:
    """以流式方式校验安装包，避免更新器信任外部传入的文件。"""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def _wait_for_parent_exit(process_id: int, timeout_seconds: int) -> bool:
    """等待指定 Windows 进程退出，避免安装器替换仍在执行的 EXE。"""

    if os.name != "nt":
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                os.kill(process_id, 0)
            except OSError:
                return True
            time.sleep(0.2)
        return False

    import ctypes

    synchronize = 0x00100000
    wait_object_0 = 0x00000000
    wait_timeout = 0x00000102
    process = ctypes.windll.kernel32.OpenProcess(
        synchronize,
        False,
        process_id,
    )
    if not process:
        # 找不到进程通常表示它已经在启动更新器前退出。
        return True
    try:
        result = ctypes.windll.kernel32.WaitForSingleObject(
            process,
            timeout_seconds * 1000,
        )
        return result == wait_object_0
    finally:
        ctypes.windll.kernel32.CloseHandle(process)


def main() -> int:
    """更新器 CLI 入口；任何失败都保留旧安装，不删除原应用。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument("--installer", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--restart-executable", required=True)
    arguments = parser.parse_args()

    # 先互斥，再做耗时哈希校验和等待。第二个更新器必须立即退出，由首个
    # 更新器统一完成安装和重启，不能让两个安装器竞争同一安装目录。
    update_lock = _acquire_update_process_lock()
    if update_lock is None:
        return 0

    try:
        return _run_update(arguments)
    finally:
        update_lock.release()


def _installer_command(installer_path: Path) -> list[str]:
    """构造保留 Inno Setup 安装进度窗口的受控安装命令。"""

    return [
        str(installer_path),
        # /SILENT 不展示向导页，但保留标准安装进度窗口；不能使用
        # /VERYSILENT，否则主程序退出后用户会误以为应用异常关闭。
        "/SILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        # 自动更新不能由用户中途取消，否则安装器退出后容易被误认为旧版
        # 已正常重启。用户仍可在更新完成后使用新版应用。
        "/NOCANCEL",
    ]


def _run_update(arguments: argparse.Namespace) -> int:
    """在已取得唯一锁后执行校验、可见安装和一次重启。"""

    installer_path = Path(arguments.installer)
    restart_path = Path(arguments.restart_executable)
    if not installer_path.is_file():
        return 2
    if _calculate_sha256(installer_path) != arguments.sha256.lower():
        return 3
    if not _wait_for_parent_exit(arguments.parent_pid, timeout_seconds=120):
        return 4

    completed = subprocess.run(_installer_command(installer_path), check=False)
    if completed.returncode != 0:
        # 安装失败时尽量恢复原本仍可执行的版本。
        if restart_path.is_file():
            subprocess.Popen([str(restart_path)], close_fds=True)
        return completed.returncode

    if restart_path.is_file():
        subprocess.Popen([str(restart_path)], close_fds=True)
        return 0
    return 5


if __name__ == "__main__":
    sys.exit(main())
