"""运行期安全诊断的回归测试。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from infrastructure.diagnostics import write_ai_tagging_failure_diagnostic


class DiagnosticsTest(unittest.TestCase):
    """确保诊断文件可定位异常，同时绝不泄露异常正文中的敏感内容。"""

    def test_ai_tagging_failure_snapshot_excludes_exception_message(self):
        """异常正文即使含敏感字串，也只能记录异常类型和固定阶段。"""

        with TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "failure.jsonl"
            write_ai_tagging_failure_diagnostic(
                RuntimeError("credential=should-never-be-written"),
                stage="正在进行 AI 三模型判断...",
                analysis_generation=7,
                tagging_generation=3,
                output_path=output_path,
            )

            content = output_path.read_text(encoding="utf-8")
            record = json.loads(content)

        self.assertEqual(record["event"], "AI_TAGGING_PIPELINE_FAILED")
        self.assertEqual(record["exceptionType"], "RuntimeError")
        self.assertEqual(record["exceptionCode"], "RUNTIMEERROR")
        self.assertEqual(record["analysisGeneration"], 7)
        self.assertEqual(record["taggingGeneration"], 3)
        self.assertNotIn("credential", content)

    def test_known_value_error_uses_fixed_safe_code(self):
        """已知输入契约错误可定位，但 JSON 不保存异常正文。"""

        with TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "failure.jsonl"
            write_ai_tagging_failure_diagnostic(
                ValueError("同一 month 中不允许重复正式 word"),
                stage="正在查询历史标签缓存...",
                analysis_generation=8,
                tagging_generation=4,
                output_path=output_path,
            )

            content = output_path.read_text(encoding="utf-8")
            record = json.loads(content)

        self.assertEqual(record["exceptionCode"], "DUPLICATE_MONTHLY_WORD")
        self.assertNotIn("同一 month 中不允许重复正式 word", content)


if __name__ == "__main__":
    unittest.main()
