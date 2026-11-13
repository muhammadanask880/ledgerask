"""
Standalone MCP server - exposes the exact same safe, read-only tools as the
main web app, but over the real Model Context Protocol, so any MCP client
(Claude Desktop, etc.) can use them directly.

Run it directly to sanity check it starts:
    python mcp_server/server.py

To wire it into Claude Desktop, see claude_desktop_config.example.json and
the README.
"""
import sys
from pathlib import Path

# Lets this file be run directly (e.g. by Claude Desktop) regardless of
# what the current working directory is, by putting the project root on
# sys.path before importing the `app` package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.mcpserver import MCPServer

from app import tools

mcp = MCPServer("LedgerAsk")

# The audit log records who asked for each query. MCP clients like Claude
# Desktop don't have a logged-in LedgerAsk user, so they're logged under a
# fixed label instead of a real account.
MCP_CLIENT_LABEL = "mcp-client"


@mcp.tool()
def list_tables() -> dict:
    """List every table available in the business database."""
    return tools.list_tables(user_email=MCP_CLIENT_LABEL, question="list_tables")


@mcp.tool()
def describe_table(table_name: str) -> dict:
    """Get the column names and types for one specific table."""
    return tools.describe_table(
        table_name, user_email=MCP_CLIENT_LABEL, question=f"describe_table({table_name})"
    )


@mcp.tool()
def run_select_query(sql: str) -> dict:
    """Run a read-only SQL SELECT query against the business database.
    Only a single SELECT statement is allowed - writes are rejected."""
    return tools.run_select_query(sql, user_email=MCP_CLIENT_LABEL, question=sql)


@mcp.tool()
def revenue_by_month(months: int = 12) -> dict:
    """Total revenue and order count per calendar month, most recent first."""
    return tools.revenue_by_month(
        months, user_email=MCP_CLIENT_LABEL, question=f"revenue_by_month({months})"
    )


@mcp.tool()
def top_products(limit: int = 5, since_days: int = 90) -> dict:
    """The best-selling products by revenue within a recent time window."""
    return tools.top_products(
        limit, since_days, user_email=MCP_CLIENT_LABEL, question="top_products"
    )


@mcp.tool()
def sales_by_region(months: int = 3) -> dict:
    """Monthly revenue broken down by region for the last N months."""
    return tools.sales_by_region(months, user_email=MCP_CLIENT_LABEL, question="sales_by_region")


if __name__ == "__main__":
    mcp.run()
