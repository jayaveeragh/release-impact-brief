# Status

Last updated: 2026-11-01

## Where we are

Phase 0: Foundation, all steps done. Awaiting owner commit for step 3.

| Step | State |
|---|---|
| 1. Skeleton: lint, tests, CI, README, license, docs | done (ac8256d) |
| 2. Fact store schema with tests | done (88eaa5b) |
| 3. Safe GitHub fetch + `rib fetch` CLI | done, awaiting owner commit |

Done criterion met on 2026-11-01: `rib fetch` on both test repos prints SHA and file counts, and the temp clone is removed afterwards.

| Repo | Commit | Files | .java |
|---|---|---|---|
| spring-projects/spring-petclinic | 500158f732419217507c7656904b8e6aa1bcc0d6 | 132 | 50 |
| spring-petclinic/spring-petclinic-microservices | e8b747ab522721076cacb5cf688eb8a54ba8c5c9 | 200 | 64 |

These SHAs are the HEADs on 2026-11-01. They are candidates for pinning as Phase 1 fixtures.

## Working agreement

- The owner commits manually from their own account. Claude never commits and never adds a Co-Authored-By line.
- One phase at a time. A plan is approved before coding. Each commit-sized change stops for review.

## Next

Phase 1 (Java facts): propose a plan and wait for approval before coding.
