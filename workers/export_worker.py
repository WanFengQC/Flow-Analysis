"""在 QThread 中执行 Final Analysis Excel 文件写入。"""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from services.export_service import ExportService


class ExportWorker(QObject):
    """只执行文件导出，不访问 Controller、View 或任何外部业务服务。"""

    succeeded = Signal(str)
    failed = Signal(str)
    progress = Signal(str)
    finished = Signal()

    def __init__(
        self,
        export_service: ExportService,
        rows: Sequence[Mapping[str, Any]],
        output_path: str | Path,
        analysis_raw: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        reversing_results: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
        analysis_word_results: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        analysis_asin_word_results: Mapping[
            str,
            Mapping[str, Sequence[Mapping[str, Any]]],
        ] | None = None,
    ) -> None:
        """保存 Controller 传入的不可变快照和用户选择的目标路径。"""

        super().__init__()
        self._export_service = export_service
        self._rows = rows
        self._output_path = output_path
        # Controller 在创建 Worker 前完成只读结构快照；导出线程绝不回访
        # Controller 的可变状态，也不会重新调用业务服务。
        self._analysis_raw = analysis_raw or {}
        self._reversing_results = reversing_results or {}
        self._analysis_word_results = analysis_word_results or {}
        self._analysis_asin_word_results = analysis_asin_word_results or {}

    @Slot()
    def run(self) -> None:
        """在后台写入 xlsx；无论结果如何都通知 QThread 退出。"""

        try:
            output = self._export_service.export_analysis(
                rows=self._rows,
                output_path=self._output_path,
                analysis_raw=self._analysis_raw,
                reversing_results=self._reversing_results,
                analysis_word_results=self._analysis_word_results,
                analysis_asin_word_results=self._analysis_asin_word_results,
                progress_callback=self.progress.emit,
            )
        except Exception as exc:
            self.failed.emit(str(exc) or "未知文件写入错误")
        else:
            self.succeeded.emit(str(output))
        finally:
            self.finished.emit()
