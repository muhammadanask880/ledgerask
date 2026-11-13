"""
Read-only access to the business database (customers/orders/products/...).

Two independent layers of protection:
  1. app.safety validates the SQL text itself (SELECT-only, no stacked
     statements, no write/DDL keywords).
  2. This module opens the SQLite connection in URI read-only mode, so even
     a query that somehow slipped past validation would be rejected by the
     database engine itself.

A hard wall-clock timeout is enforced by running the query on a background
thread and calling `Connection.interrupt()` if it overruns - this is the
standard way to cancel a running SQLite query from another thread.
"""
import sqlite3
import threading
from typing import Any

from app.config import settings

DEFAULT_MAX_ROWS = 200
DEFAULT_TIMEOUT_SECONDS = 5.0


def _get_readonly_connection() -> sqlite3.Connection:
    db_path = settings.business_db_full_path
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def execute_select(
    sql: str,
    max_rows: int = DEFAULT_MAX_ROWS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[list[str], list[sqlite3.Row], bool]:
    """Execute an already-validated, read-only SELECT with a hard timeout.

    Returns (columns, rows, truncated).
    """
    conn = _get_readonly_connection()
    outcome: dict[str, Any] = {}

    def _run() -> None:
        try:
            cursor = conn.execute(sql)
            columns = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchmany(max_rows + 1)
            outcome["columns"] = columns
            outcome["rows"] = rows
        except Exception as exc:  # noqa: BLE001 - re-raised on the caller's thread below
            outcome["error"] = exc

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(timeout_seconds)

    if worker.is_alive():
        conn.interrupt()
        worker.join(1)
        conn.close()
        raise TimeoutError(f"Query timed out after {timeout_seconds} seconds.")

    conn.close()

    if "error" in outcome:
        raise outcome["error"]

    columns = outcome["columns"]
    rows = outcome["rows"]
    truncated = len(rows) > max_rows
    if truncated:
        rows = rows[:max_rows]
    return columns, rows, truncated
