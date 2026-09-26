# Roadmap

Ideas that are not built yet. Open roadmap work is tracked as GitHub issues.

- **Author page detail** (bio, photo, follower count). Not currently exposed cleanly: the author page is legacy server-rendered HTML with no structured JSON, and there's no discoverable GraphQL contributor-detail query, so this would require brittle DOM scraping. `author_books` links to the page (`author_url`) instead.
- **Caching layer for repeated lookups.** The discovery tools each resolve the book first; a small TTL cache would cut duplicate GraphQL calls. Today the only cache is the per-process GraphQL key and endpoint (`client.graphql_config`).
