"""grounded-mcp — an evals-first MCP server for markdown knowledge vaults.

Three guardrails, all proven by the shipped eval suite:
  * citations  — every content-bearing return carries a stable citation id
  * abstention — the server says "not found" rather than serving weak matches
  * entitlements — denied content is never indexed for a principal, so it
    cannot leak through scores, snippets, or rankings
"""

__version__ = "0.2.0"
