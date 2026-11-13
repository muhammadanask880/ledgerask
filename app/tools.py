"""
The fixed menu of safe, read-only tools the AI is allowed to call - this is
the "MCP-style" tool layer. Every tool ultimately goes through
business_db.execute_select (read-only connection) and every call is logged
via logging_config.log_query, regardless of which tool was used.

TOOL_SPECS below is the OpenAI function-calling schema describing this menu
to the model. app.agent.py sends TOOL_SPECS to OpenAI and dispatches any
tool_calls it gets back through call_tool().
"""
import time
from typing import Any, Optional

from app.business_db import execute_select
from app.logging_config import log_query
from app.safety import UnsafeQueryError, enforce_row_limit, validate_select_only

MAX_ROWS = 200


def _run_sql(sql: str, *, user_email: str, question: str, tool_name: str) -> dict[str, Any]:
    start = time.perf_counter()
    try:
        columns, rows, truncated = execute_select(sql, max_rows=MAX_ROWS)
        duration_ms = (time.perf_counter() - start) * 1000
        row_dicts = [dict(zip(columns, row)) for row in rows]
        log_query(
            user_email=user_email, question=question, tool=tool_name, sql=sql,
            row_count=len(row_dicts), duration_ms=duration_ms, status="ok",
        )
        return {"sql": sql, "columns": columns, "rows": row_dicts, "truncated": truncated}
    except Exception as exc:
        duration_ms = (time.perf_counter() - start) * 1000
        log_query(
            user_email=user_email, question=question, tool=tool_name, sql=sql,
            row_count=None, duration_ms=duration_ms, status="error", error=str(exc),
        )
        raise


def list_tables(*, user_email: str, question: str) -> dict[str, Any]:
    sql = "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    return _run_sql(sql, user_email=user_email, question=question, tool_name="list_tables")


def describe_table(table_name: str, *, user_email: str, question: str) -> dict[str, Any]:
    # table_name is model-supplied, so it is checked against the live list of
    # real tables (exact match) before being interpolated - PRAGMA doesn't
    # support bound parameters, so an allowlist is the safe way to do this.
    tables_result = list_tables(user_email=user_email, question=question)
    valid_tables = {row["name"] for row in tables_result["rows"]}
    if table_name not in valid_tables:
        raise ValueError(f"Unknown table: {table_name!r}. Known tables: {sorted(valid_tables)}")
    sql = f"PRAGMA table_info({table_name})"
    return _run_sql(sql, user_email=user_email, question=question, tool_name="describe_table")


def run_select_query(sql: str, *, user_email: str, question: str) -> dict[str, Any]:
    try:
        validated = validate_select_only(sql)
    except UnsafeQueryError as exc:
        log_query(
            user_email=user_email, question=question, tool="run_select_query", sql=sql,
            row_count=None, duration_ms=0.0, status="blocked", error=str(exc),
        )
        raise
    final_sql = enforce_row_limit(validated, MAX_ROWS)
    return _run_sql(final_sql, user_email=user_email, question=question, tool_name="run_select_query")


def revenue_by_month(months: int = 12, *, user_email: str, question: str) -> dict[str, Any]:
    """Revenue for the last N *complete* calendar months. The month still in
    progress (if any) is deliberately excluded so month-over-month numbers
    are never skewed by comparing a full month against a partial one."""
    months = max(1, min(int(months), 60))
    sql = f"""
        WITH bounds AS (
            SELECT date(MAX(order_date), 'start of month') AS current_month_start FROM orders
        )
        SELECT strftime('%Y-%m', o.order_date) AS month,
               ROUND(SUM(oi.quantity * oi.unit_price), 2) AS revenue,
               COUNT(DISTINCT o.id) AS order_count
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id, bounds
        WHERE o.status = 'completed'
          AND o.order_date < bounds.current_month_start
          AND o.order_date >= date(bounds.current_month_start, '-{months} months')
        GROUP BY month
        ORDER BY month DESC
    """
    return _run_sql(sql, user_email=user_email, question=question, tool_name="revenue_by_month")


def top_products(limit: int = 5, since_days: int = 90, *, user_email: str, question: str) -> dict[str, Any]:
    limit = max(1, min(int(limit), 50))
    since_days = max(1, min(int(since_days), 730))
    sql = f"""
        SELECT p.name AS product, p.category AS category,
               SUM(oi.quantity) AS units_sold,
               ROUND(SUM(oi.quantity * oi.unit_price), 2) AS revenue
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        JOIN products p ON p.id = oi.product_id
        WHERE o.status = 'completed'
          AND o.order_date >= date((SELECT MAX(order_date) FROM orders), '-{since_days} days')
        GROUP BY p.id
        ORDER BY revenue DESC
        LIMIT {limit}
    """
    return _run_sql(sql, user_email=user_email, question=question, tool_name="top_products")


def sales_by_region(months: int = 3, *, user_email: str, question: str) -> dict[str, Any]:
    """Monthly revenue per region for the last N *complete* calendar months.
    The month still in progress (if any) is deliberately excluded - without
    this, a partial current month would look like a "drop" in every region
    just because it hasn't finished yet."""
    months = max(1, min(int(months), 24))
    sql = f"""
        WITH bounds AS (
            SELECT date(MAX(order_date), 'start of month') AS current_month_start FROM orders
        )
        SELECT r.name AS region,
               strftime('%Y-%m', o.order_date) AS month,
               ROUND(SUM(oi.quantity * oi.unit_price), 2) AS revenue,
               COUNT(DISTINCT o.id) AS order_count
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        JOIN regions r ON r.id = o.region_id, bounds
        WHERE o.status = 'completed'
          AND o.order_date < bounds.current_month_start
          AND o.order_date >= date(bounds.current_month_start, '-{months} months')
        GROUP BY r.name, month
        ORDER BY month DESC, region
    """
    return _run_sql(sql, user_email=user_email, question=question, tool_name="sales_by_region")


# ---------------------------------------------------------------------------
# OpenAI function-calling schema + dispatch table
# ---------------------------------------------------------------------------

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_tables",
            "description": "List every table available in the business database.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_table",
            "description": "Get the column names and types for one specific table.",
            "parameters": {
                "type": "object",
                "properties": {"table_name": {"type": "string"}},
                "required": ["table_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_select_query",
            "description": (
                "Run a read-only SQL SELECT query against the business database. "
                "Only a single SELECT statement is allowed - any write/DDL keyword is "
                "rejected. Use this for anything not already covered by the shortcut tools."
            ),
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string", "description": "A single SELECT statement."}},
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "revenue_by_month",
            "description": (
                "Total revenue and order count per complete calendar month, most recent "
                "first. The month still in progress is excluded, so the most recent row "
                "is always a full month."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "months": {"type": "integer", "description": "How many recent months to return (default 12)."}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "top_products",
            "description": "The best-selling products by revenue within a recent time window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "How many top products to return (default 5)."},
                    "since_days": {"type": "integer", "description": "How many days back to look (default 90)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sales_by_region",
            "description": (
                "Monthly revenue broken down by region for the last N complete calendar "
                "months (the in-progress month is excluded) - use this to spot which "
                "region grew or dropped month-over-month, and by how much."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "months": {"type": "integer", "description": "How many recent months to include (default 3)."}
                },
                "required": [],
            },
        },
    },
]

_DISPATCH = {
    "list_tables": lambda args, ctx: list_tables(**ctx),
    "describe_table": lambda args, ctx: describe_table(args["table_name"], **ctx),
    "run_select_query": lambda args, ctx: run_select_query(args["sql"], **ctx),
    "revenue_by_month": lambda args, ctx: revenue_by_month(int(args.get("months", 12)), **ctx),
    "top_products": lambda args, ctx: top_products(
        int(args.get("limit", 5)), int(args.get("since_days", 90)), **ctx
    ),
    "sales_by_region": lambda args, ctx: sales_by_region(int(args.get("months", 3)), **ctx),
}


def call_tool(name: str, arguments: dict[str, Any], *, user_email: str, question: str) -> dict[str, Any]:
    if name not in _DISPATCH:
        raise ValueError(f"Unknown tool: {name}")
    ctx = {"user_email": user_email, "question": question}
    return _DISPATCH[name](arguments, ctx)
