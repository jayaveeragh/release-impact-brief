# Decisions

Format: `YYYY-MM-DD — what — why — status (active / reversed / superseded)`

- 2026-11-01 — Use `docs/DECISIONS.md` + `docs/STATUS.md` as the only project journal (no separate PROJECT_NOTES.md) — avoids two overlapping logs — active
- 2026-11-01 — License: MIT — simple and common for a portfolio project; Apache-2.0 was the alternative (explicit patent grant) — active, owner may change
- 2026-11-01 — Python packaging via `venv` + `pip` + `pyproject.toml` (hatchling), not `uv` — `uv` isn't installed on the dev machine and installing tools wasn't requested; easy to switch later — active
- 2026-11-01 — Python floor 3.12; CI tests 3.12 and 3.14 — 3.12 is the oldest still widely deployed, 3.14 is the local dev version — active
- 2026-11-01 — Tooling: ruff (lint + format), mypy strict, pytest; versions checked on PyPI on this date (ruff 0.17.3, pytest 9.2.0, mypy 2.4.1, hatchling 1.33.0; GitHub Actions checkout/setup-python v7) — current at time of setup — active
- 2026-11-01 — No frontend scaffold until Phase 4 — nothing to render before then; avoids dead config — active
- 2026-11-01 — Fetch by cloning one pinned commit to a local temp folder, not per-file GitHub API calls — one request vs thousands; unauthenticated API allows 60 requests/hour — active
- 2026-11-01 — Proposed: run a small evaluation (about 5 real fixes) before Phase 7, full set of about 30 in Phase 8 — so the impact design is guided by data, not built blind — proposed, revisit before Phase 7
- 2026-11-01 — Note: Defects4J is mostly library code (no Spring endpoints), so it tests method-level impact only; endpoint/flow impact needs real fix commits from Spring repos — unverified assumption, confirm in Phase 8 — active
- 2026-11-01 — Fact store uses stdlib `sqlite3`, no ORM — three small tables; an ORM adds a dependency without saving code — active
- 2026-11-01 — Node kinds and confidence labels enforced twice: Python enums and SQL CHECK constraints — the DB stays honest even if a future code path skips the dataclasses — active
- 2026-11-01 — Edges have their own IDs — view specs reference edges as `fact:<id>` (see brief example `fact:edge-91`) — active
- 2026-11-01 — Edges must point to existing nodes in the same snapshot (composite foreign keys) — unresolved calls must target an explicit `external_system` / unknown node, never a dangling ID — active
- 2026-11-01 — Edge `kind` is free text for now, not an enum — the needed set (calls, implements, injects, handles, …) isn't known until Phase 1 resolvers exist; tighten to an enum then — active, revisit in Phase 1
- 2026-11-01 — Commit SHA must be a full 40-char lowercase hex — short SHAs can become ambiguous, breaking the cache key — active
