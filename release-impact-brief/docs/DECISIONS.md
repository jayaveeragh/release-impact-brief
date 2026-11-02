# Decisions

Format: `YYYY-MM-DD — what — why — status (active / reversed / superseded)`

- 2026-11-01 — Use `docs/DECISIONS.md` + `docs/STATUS.md` as the only project journal (no separate PROJECT_NOTES.md) — avoids two overlapping logs — reversed 2026-11-01: owner's global rules require PROJECT_NOTES.md in every project; see next entry
- 2026-11-01 — Two files with separate roles: `docs/DECISIONS.md` holds decisions only; `PROJECT_NOTES.md` holds assumptions, measurements, surprises and dead ends — the brief asks for DECISIONS.md and the owner's global rules ask for PROJECT_NOTES.md; splitting by role avoids duplicate entries — active
- 2026-11-01 — License: MIT — simple and common for a portfolio project; Apache-2.0 was the alternative (explicit patent grant) — active, owner may change
- 2026-11-01 — Python packaging via `venv` + `pip` + `pyproject.toml` (hatchling), not `uv` — `uv` isn't installed on the dev machine and installing tools wasn't requested; easy to switch later — active
- 2026-11-01 — Python floor 3.12; CI tests 3.12 and 3.14 — 3.12 is the oldest still widely deployed, 3.14 is the local dev version — active
- 2026-11-01 — Tooling: ruff (lint + format), mypy strict, pytest; versions checked on PyPI on this date (ruff 0.17.3, pytest 9.2.0, mypy 2.4.1, hatchling 1.33.0; GitHub Actions checkout/setup-python v7) — current at time of setup — active
- 2026-11-01 — No frontend scaffold until Phase 4 — nothing to render before then; avoids dead config — active
- 2026-11-01 — Fetch by cloning one pinned commit to a local temp folder, not per-file GitHub API calls — one request vs thousands; unauthenticated API allows 60 requests/hour — active
- 2026-11-01 — Proposed: run a small evaluation (about 5 real fixes) before Phase 7, full set of about 30 in Phase 8 — so the impact design is guided by data, not built blind — proposed, revisit before Phase 7
- 2026-11-01 — Fact store uses stdlib `sqlite3`, no ORM — three small tables; an ORM adds a dependency without saving code — active
- 2026-11-01 — Node kinds and confidence labels enforced twice: Python enums and SQL CHECK constraints — the DB stays honest even if a future code path skips the dataclasses — active
- 2026-11-01 — Edges have their own IDs — view specs reference edges as `fact:<id>` (see brief example `fact:edge-91`) — active
- 2026-11-01 — Edges must point to existing nodes in the same snapshot (composite foreign keys) — unresolved calls must target an explicit `external_system` / unknown node, never a dangling ID — active
- 2026-11-01 — Edge `kind` is free text for now, not an enum — the needed set (calls, implements, injects, handles, …) isn't known until Phase 1 resolvers exist; tighten to an enum then — active, revisit in Phase 1
- 2026-11-01 — Commit SHA must be a full 40-char lowercase hex — short SHAs can become ambiguous, breaking the cache key — active
- 2026-11-01 — URL must be exactly `https://github.com/<owner>/<repo>`; branch/tag/SHA goes in `--ref` — `/tree/<branch>` URLs are ambiguous when branch names contain `/`; a strict URL shape also blocks other hosts, credentials, ports and `file://` — active
- 2026-11-01 — Pinned fetch = `ls-remote` → full SHA, then `git init` + `fetch --depth 1 <sha>` — downloads one commit only; GitHub allows fetching by SHA — active
- 2026-11-01 — Git hardening: user/system git config ignored, only https protocol allowed, `core.symlinks=false`, hooksPath=/dev/null, no submodules, LFS smudge skipped, no terminal prompts, timeout on every git call — repo is untrusted input; symlinks become plain text files so nothing can point outside the clone — active
- 2026-11-01 — Limits: 20,000 files, 200 MB total, 1 MB per file, 120 s per git call — arbitrary, never measured (repo sizes in PROJECT_NOTES.md) — active
- 2026-11-01 — Over-size single files are skipped and counted, not fatal; file-count and total-size overruns are fatal — one big binary shouldn't block analysis of a normal repo — active
- 2026-11-01 — Network test (real GitHub fetch) is marked `network` and skipped by default (`pytest -m network` to run) — keeps CI offline and deterministic — active
- 2026-11-01 — Library and built-in calls (JDK such as `String.trim`, framework such as Spring Data `save`) are noted as library calls, never traced into — owner: only user-defined code (classes, interfaces, abstractions) is ours to explain; built-ins are mentioned, not explained — active
- 2026-11-01 — Answer key for Phase 1 written by hand in `tests/fixtures/spring-petclinic/expected.json` before the tool could extract endpoints/entities — the brief requires expected results before tool output, so the key can't be bent to fit the tool — active
- 2026-11-01 — Java fact IDs are readable and stable: `java:class:<fqn>`, `java:method:<class>#<name>(<param types>)`, `java:field:<class>#<name>` — overloads stay distinct; IDs survive re-runs, which view specs and caching need — active
- 2026-11-01 — If two files declare the same qualified name (e.g. two modules), the second ID gets `@<file>` appended — keeps both facts instead of crashing on a duplicate key; none seen in either petclinic repo — active
- 2026-11-01 — Files with syntax errors are still extracted and listed as "facts may be partial" — Tree-sitter recovers from errors; hiding the file would lose facts, hiding the warning would overclaim — active
- 2026-11-01 — Enum constants and record components stored as fields (flagged `enum_constant` / `record_component`) — Phase 5 switch branches need enum constants; record components behave as fields — active
- 2026-11-01 — `rib facts` caches by repo + SHA: resolve SHA first, skip clone and parse if the snapshot already has facts; nodes and edges are written in one transaction — a half-written snapshot would otherwise look cached — active
- 2026-11-01 — Pin `tree-sitter>=0.25.2,<0.26` — 0.26.0 segfaults on spring-petclinic test classes (details in PROJECT_NOTES.md) — active, retry when a newer release is out
