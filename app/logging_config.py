"""
Audit logging: every question the AI answers and the exact SQL it ran gets
written to a log file as one JSON object per line. This is the proof trail
that makes the "it's safe, nothing destructive ever happened" claim
checkable rather than just asserted.
"""
import json
import logging
import time
from typing import Optional

from app.config import settings

_logger: Optional[logging.Logger] = None


def _get_query_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    settings.query_log_full_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ledgerask.queries")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.FileHandler(settings.query_log_full_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
    _logger = logger
    return logger


def log_query(
    *,
    user_email: Optional[str],
    question: Optional[str],
    tool: str,
    sql: Optional[str],
    row_count: Optional[int],
    duration_ms: float,
    status: str,
    error: Optional[str] = None,
) -> None:
    entry = {
        "timestamp": time.time(),
        "user": user_email,
        "question": question,
        "tool": tool,
        "sql": sql,
        "row_count": row_count,
        "duration_ms": round(duration_ms, 2),
        "status": status,
        "error": error,
    }
    _get_query_logger().info(json.dumps(entry, ensure_ascii=False))
