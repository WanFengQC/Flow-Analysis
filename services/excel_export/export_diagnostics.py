"""Excel 导出的开发期性能诊断，严禁保存任何业务文本或认证信息。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from threading import Event, Thread
import os


class MemoryPeakSampler:
    """仅在开发测试中采样本进程工作集峰值，不追踪 openpyxl 对象。"""

    def __init__(self) -> None:
        self._stop_event = Event()
        self._peak_bytes = self._working_set_bytes()
        self._thread = Thread(
            target=self._sample,
            name="ExcelExportMemorySampler",
            daemon=True,
        )

    def start(self) -> None:
        """启动低频采样；正式导出默认不创建该线程。"""

        self._thread.start()

    def stop(self) -> int | None:
        """停止采样并返回本次导出期间观察到的峰值。"""

        self._stop_event.set()
        self._thread.join(timeout=1.0)
        return self._peak_bytes

    def _sample(self) -> None:
        while not self._stop_event.wait(0.02):
            current = self._working_set_bytes()
            if current is not None:
                self._peak_bytes = max(self._peak_bytes or 0, current)

    @staticmethod
    def _working_set_bytes() -> int | None:
        """读取 Windows 当前进程工作集；非 Windows 环境安全降级为空。"""

        if os.name != "nt":
            return None
        try:
            import ctypes
            from ctypes import wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            get_current_process = ctypes.windll.kernel32.GetCurrentProcess
            get_current_process.restype = wintypes.HANDLE
            get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
            get_process_memory_info.argtypes = (
                wintypes.HANDLE,
                ctypes.c_void_p,
                wintypes.DWORD,
            )
            get_process_memory_info.restype = wintypes.BOOL
            success = get_process_memory_info(
                get_current_process(),
                ctypes.byref(counters),
                counters.cb,
            )
            return int(counters.WorkingSetSize) if success else None
        except Exception:
            return None


@dataclass(slots=True)
class ExportDiagnostics:
    """只保留聚合性能数字，供日志和自动化测试检查。"""

    phase_seconds: dict[str, float] = field(default_factory=dict)
    data_rows: dict[str, int] = field(default_factory=dict)
    sheet_count: int = 0
    output_bytes: int = 0
    peak_memory_bytes: int | None = None
    total_seconds: float = 0.0
    _started_at: float = field(default_factory=perf_counter, repr=False)

    _REQUIRED_PHASES = (
        "load_workbook",
        "SUMMARY_result_build",
        "SUMMARY_raw_build",
        "SUMMARY_result_list_build",
        "ASIN_result_build",
        "ASIN_raw_build",
        "copy_worksheet",
        "workbook_save",
    )
    _REQUIRED_DATASETS = (
        "SUMMARY_result",
        "SUMMARY_raw",
        "SUMMARY_result_list",
        "ASIN_result",
        "ASIN_raw",
    )

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        """累计同一阶段的耗时，支持多个 ASIN Sheet 聚合统计。"""

        started_at = perf_counter()
        try:
            yield
        finally:
            self.phase_seconds[name] = (
                self.phase_seconds.get(name, 0.0)
                + perf_counter() - started_at
            )

    def finish(self) -> None:
        """写入总耗时；调用方在文件实际保存后调用。"""

        for phase_name in self._REQUIRED_PHASES:
            self.phase_seconds.setdefault(phase_name, 0.0)
        for dataset_name in self._REQUIRED_DATASETS:
            self.data_rows.setdefault(dataset_name, 0)
        self.total_seconds = perf_counter() - self._started_at

    def to_log_fields(self) -> dict[str, object]:
        """返回可安全写日志的聚合字段，不包含任何业务行内容。"""

        return {
            "phase_seconds": {
                name: round(value, 3)
                for name, value in self.phase_seconds.items()
            },
            "data_rows": dict(self.data_rows),
            "sheet_count": self.sheet_count,
            "output_bytes": self.output_bytes,
            "peak_memory_bytes": self.peak_memory_bytes,
            "total_seconds": round(self.total_seconds, 3),
        }
