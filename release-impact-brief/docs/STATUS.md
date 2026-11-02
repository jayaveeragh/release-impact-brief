# Status

Last updated: 2026-11-01

## Where we are

Phase 0: Foundation.

| Step | State |
|---|---|
| 1. Skeleton: lint, tests, CI, README, license, docs | done (ac8256d) |
| 2. Fact store schema with tests | done, awaiting owner commit |
| 3. Safe GitHub fetch + `rib fetch` CLI | next |

Phase 0 is done when `rib fetch <github url>` fetches a repo safely and prints its commit SHA and file counts.

## Working agreement

- The owner commits manually from their own account. Claude never commits and never adds a Co-Authored-By line.
- One phase at a time. A plan is approved before coding. Each commit-sized change stops for review.

## Next

Commit 3: safe GitHub fetch (URL validation, pinned shallow clone, size/file limits, cleanup) and a `rib fetch` CLI that prints commit SHA and file counts.
