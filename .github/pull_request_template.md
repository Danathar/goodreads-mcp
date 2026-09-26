## What

<!-- What does this change do, and why? -->

## Checks

- [ ] `pytest -q` passes locally
- [ ] `npx -y @anthropic-ai/mcpb@2.1.2 validate ./manifest.json` passes (if `manifest.json` changed)
- [ ] Version in `pyproject.toml` / `manifest.json` left unchanged (only the release workflow's `prepare` pull request changes it)
