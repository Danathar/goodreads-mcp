# Roadmap

Ideas that are not built yet. Open roadmap work is tracked as GitHub issues.

How these ideas fit the project's direction, and the signals for whether it is on track, are in [strategy.md](strategy.md).

- **Author page detail** (bio, photo, follower count). Not currently exposed cleanly: the author page is legacy server-rendered HTML with no structured JSON, and there's no discoverable GraphQL contributor-detail query, so this would require brittle DOM scraping. `author_books` links to the page (`author_url`) instead.
- **Caching layer for repeated lookups.** The discovery tools each resolve the book first; a small TTL cache would cut duplicate GraphQL calls. Today the only cache is the per-process GraphQL key and endpoint (`client.graphql_config`).
- **`mcp` 2.x migration.** The server stays on the 1.x line (`mcp[cli]>=1.21.1,<2` in `pyproject.toml`) until this is done: 2.x removed `mcp.server.fastmcp` (`FastMCP` became `MCPServer`, imported from `mcp.server.mcpserver`), so `import goodreads_mcp.server` fails there. The migration touches `OffLoopFastMCP(FastMCP)` and its `add_tool` override (the mechanism behind #92 and #206), the private `mcp._mcp_server.version` assignment (#214), `ToolAnnotations` and the `instructions=` argument, and the stdio ping test in `tests/e2e/test_smoke_live.py`, which uses the SDK's client side. It is Tier 2 (tool registration and the MCP contract) and the live ping test is the proof. A non-blocking CI lane that installs `mcp>=2` and runs the offline suite would list the failures ahead of time; it is not a required check and is not added yet.
