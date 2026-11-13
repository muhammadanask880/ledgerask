"""Tests for the safety gate in app/safety.py - these must pass before this
project can honestly be called "safe to point at a real database"."""
import pytest

from app.safety import UnsafeQueryError, enforce_row_limit, validate_select_only


def test_allows_simple_select():
    assert validate_select_only("SELECT * FROM orders") == "SELECT * FROM orders"


def test_allows_select_with_trailing_semicolon():
    assert validate_select_only("SELECT * FROM orders;") == "SELECT * FROM orders"


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO orders (id) VALUES (1)",
        "UPDATE orders SET status = 'completed'",
        "DELETE FROM orders",
        "DROP TABLE orders",
        "ALTER TABLE orders ADD COLUMN x TEXT",
        "CREATE TABLE evil (id INTEGER)",
        "ATTACH DATABASE 'other.db' AS other",
        "PRAGMA writable_schema = 1",
        "VACUUM",
    ],
)
def test_rejects_write_and_admin_statements(sql):
    with pytest.raises(UnsafeQueryError):
        validate_select_only(sql)


def test_rejects_stacked_statements():
    with pytest.raises(UnsafeQueryError):
        validate_select_only("SELECT * FROM orders; DROP TABLE orders;")


def test_rejects_write_hidden_in_subquery():
    with pytest.raises(UnsafeQueryError):
        validate_select_only("SELECT * FROM (DELETE FROM orders RETURNING *)")


def test_rejects_non_select_statement():
    with pytest.raises(UnsafeQueryError):
        validate_select_only("EXPLAIN SELECT * FROM orders")


def test_rejects_empty_query():
    with pytest.raises(UnsafeQueryError):
        validate_select_only("   ")


def test_enforce_row_limit_appends_when_missing():
    result = enforce_row_limit("SELECT * FROM orders", 50)
    assert "LIMIT 50" in result


def test_enforce_row_limit_leaves_existing_limit_alone():
    sql = "SELECT * FROM orders LIMIT 10"
    assert enforce_row_limit(sql, 50) == sql
