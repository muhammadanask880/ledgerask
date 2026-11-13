"""
The safety gate every AI-generated SQL query must pass through before it
ever touches the database.

This is the single most important file in the project: it is what makes it
safe to let an LLM write SQL against a real company database. The rules:

  1. Exactly one statement - no stacked/"; DROP TABLE ..." tricks.
  2. The statement must start with SELECT.
  3. A fixed list of write/DDL/admin keywords is rejected outright, even if
     they appear inside a subquery or CTE.
  4. A LIMIT is always enforced, so one bad query can't return the entire
     database into the chat.

Defense in depth: even if a query somehow slipped past this validator, the
connection it runs on (see business_db.py) is opened in SQLite's read-only
URI mode, so writes fail at the database engine level too.
"""
import re

FORBIDDEN_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "ATTACH", "DETACH",
    "REPLACE", "TRUNCATE", "PRAGMA", "VACUUM", "REINDEX", "GRANT", "REVOKE",
    "EXEC", "EXECUTE", "BEGIN", "COMMIT", "ROLLBACK",
]


class UnsafeQueryError(ValueError):
    """Raised when a query fails the safety checks. Never silently swallow this."""


def validate_select_only(sql: str) -> str:
    """Raise UnsafeQueryError unless `sql` is a single, safe SELECT statement.

    Returns the trimmed query (with any single trailing semicolon removed)
    so callers always get back exactly what will be executed.
    """
    if not sql or not sql.strip():
        raise UnsafeQueryError("Empty query.")

    stripped = sql.strip()
    # Allow at most one trailing semicolon, then make sure none remain -
    # that's what blocks stacked statements like "SELECT 1; DROP TABLE x".
    if stripped.endswith(";"):
        stripped = stripped[:-1].strip()
    if ";" in stripped:
        raise UnsafeQueryError("Multiple SQL statements are not allowed.")

    if not re.match(r"(?is)^\s*SELECT\b", stripped):
        raise UnsafeQueryError("Only SELECT statements are allowed.")

    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"(?i)\b{keyword}\b", stripped):
            raise UnsafeQueryError(f"Query contains a forbidden keyword: {keyword}.")

    return stripped


def enforce_row_limit(sql: str, max_rows: int) -> str:
    """Append a LIMIT clause if the query doesn't already have one."""
    if re.search(r"(?i)\blimit\s+\d+", sql):
        return sql
    return f"{sql}\nLIMIT {max_rows}"
