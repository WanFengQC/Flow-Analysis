"""运行期安全诊断写入：仅保存可定位的非敏感元数据。"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import threading


logger = logging.getLogger(__name__)
_WRITE_LOCK = threading.Lock()
_DIAGNOSTIC_FILE = (
    Path(__file__).resolve().parent.parent
    / "runtime"
    / "diagnostics"
    / "ai_tagging_failures.jsonl"
)

# 诊断文件不能记录异常正文：其中可能混入远端响应、认证信息或产品资料。
# 对当前可预期的数据契约错误，仅保存固定错误码；未知 ValueError 一律保持
# UNCLASSIFIED，避免为了排障而突破敏感信息边界。
_SAFE_VALUE_ERROR_CODES = {
    "month 必须是非空字符串": "INVALID_WORD_RESULT_MONTH",
    "每个月的 word results 必须是序列": "INVALID_WORD_RESULTS_COLLECTION",
    "word result 必须是对象": "INVALID_WORD_RESULT_ROW",
    "word 必须是非空字符串": "INVALID_WORD_RESULT_WORD",
    "同一 month 中不允许重复正式 word": "DUPLICATE_MONTHLY_WORD",
    "AI Tagging 输入 item_id 不能重复": "DUPLICATE_TAGGING_INPUT_ID",
    "成功 Provider 的决策与输入项不完整对应": (
        "PROVIDER_DECISION_INPUT_MISMATCH"
    ),
    "同一个 Provider 不允许重复返回": "DUPLICATE_PROVIDER_OUTCOME",
    "AI_CONSENSUS 不允许含失败 Provider": "INVALID_CONSENSUS_PROVIDER_OUTCOME",
    "AI_CONSENSUS Provider 决策数量异常": "INVALID_CONSENSUS_DECISION_COUNT",
    "AI_CONSENSUS 必须保存三家 Provider 的审计摘要": (
        "INVALID_CONSENSUS_PROVIDER_SET"
    ),
}


def _safe_exception_code(exception: BaseException) -> str:
    """将已知本地契约异常映射为固定码，不泄露异常正文。"""

    if isinstance(exception, ValueError):
        return _SAFE_VALUE_ERROR_CODES.get(
            str(exception),
            "VALUE_ERROR_UNCLASSIFIED",
        )
    return type(exception).__name__.upper()


def write_ai_tagging_failure_diagnostic(
    exception: BaseException,
    *,
    stage: str,
    analysis_generation: int,
    tagging_generation: int,
    output_path: Path | None = None,
) -> None:
    """追加 AI Pipeline 失败的安全摘要；任何写入问题都不能影响正式任务。"""

    # 严禁序列化 str(exception)、traceback、HTTP response、Header 或输入 Prompt。
    # 这些内容可能意外携带认证信息或业务资料；异常类型和已知阶段已足够定位
    # 未分类故障属于哪个边界。
    record = {
        "recordedAt": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "event": "AI_TAGGING_PIPELINE_FAILED",
        "stage": stage,
        "exceptionType": type(exception).__name__,
        "exceptionCode": _safe_exception_code(exception),
        "analysisGeneration": analysis_generation,
        "taggingGeneration": tagging_generation,
        "threadName": threading.current_thread().name,
        "threadId": threading.get_ident(),
    }
    path = output_path or _DIAGNOSTIC_FILE

    try:
        with _WRITE_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError):
        # 诊断文件不是业务结果；失败时仅记录固定提示，绝不包含异常正文。
        logger.warning("AI Pipeline 安全诊断写入失败")
