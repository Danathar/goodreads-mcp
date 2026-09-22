# Verification beats plausibility

**Date:** 2026-09-16
**From:** the ACMM scaffolding series (PRs #31–#51)

## The pattern

Across ~20 PRs of mostly documentation and config, **every substantive review
finding was a claim that sounded right and wasn't.** None were typos or style.
All were assertions about the codebase that the codebase contradicted.

Concretely:

- An agent-instruction rule said "parse `__NEXT_DATA__`, never the DOM." True
  for book pages; false for `list_shelves`, which regexes HTML because shelf
  *names* have no structured surface. The rule would have told a future agent
  that working code was wrong.
- A review rubric required `_paginated_graphql_edges` for new pagination. It
  has five call sites and handles only the standard `PaginationInput` shape;
  `get_reviews` and `popular_books` legitimately paginate by hand. The rule
  could have been cited to demand an invalid refactor.
- A metrics doc shipped commands that didn't reproduce their own table.
- A permissions file gated `Edit` on workflows but not `Write`.

Each took under a minute to catch by reading the source. None would have been
caught by reasoning harder.

## What follows

**Check doc claims against source before writing them, not after.** When
writing a rule about this codebase, grep for the thing first. "Every tool uses
X" is a claim with a command attached: `grep -n X`. Run it.

**The source's own comments are not authoritative.** `client.py`'s docstring
said "four unofficial-but-stable read surfaces" and omitted the HTML scrape
`list_shelves` uses; #81 found it and #82 corrected it to five. Documentation
inherited that error, and copies of it outlived the fix. Verify against code,
not against prose about the code.

**Ship commands that were executed, not commands that look right.** A
documented one-liner is a promise. Both the `graphql_config` snippet in
`check-live-endpoints` and the settings hook were run before committing; the
hook was tested by inducing a real failure, not by inspection.

**State what could not be verified.** A cron schedule can't be proven before
it fires. Saying so is more useful than implying full coverage.

## On automated review

Codex found real defects here and also produced confident, wrong claims — one
asserted merged PRs below #31 exist, when issues #1–#30 consume the shared
number space so #31 is the first PR. Roughly half its findings were correct
but not actionable: cross-PR link breakage needing a merge order, not a code
change.

Useful, not authoritative. Verify each finding against source; reply with the
reasoning either way, including when declining. A finding resolved with "this
is correct at review time but resolves on merge" is a good outcome, not an
evasion.
