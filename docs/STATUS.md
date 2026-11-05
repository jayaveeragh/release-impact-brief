# Status

Last updated: 2026-11-04

## Where we are

Phase 0: Foundation, done (c6d1886, e4e60a5, b47eb1a). Done criterion met: `rib fetch` prints SHA and file counts for both test repos.

Phase 1: Java facts.

| Step | State |
|---|---|
| 1. Adapter interface, Java parser (classes, methods, fields, imports), `rib facts`, answer key | done (28b0edb) |
| 2. Call resolution with confidence, interface→implementation, Spring injection; library calls listed, not traced | done (bf1eeda) |
| 3. Spring endpoints | done (41abb08, together with the move to repo root; answer key: 17/17 endpoints match) |
| 4. JPA entities, relations and repositories; edge kinds as an enum; check against answer key | done (next commit; answer key: entities, mapped superclasses, relations, repositories all match) |

Phase 1 done criterion met: `rib facts` on spring-petclinic @ `500158f` matches [tests/fixtures/spring-petclinic/expected.json](../tests/fixtures/spring-petclinic/expected.json) (network tests `test_petclinic_endpoints_match_hand_written_answer_key` and `test_petclinic_data_model_matches_hand_written_answer_key`).

## Working agreement

- The owner commits manually from their own account. Claude never commits and never adds a Co-Authored-By line.
- One phase at a time. A plan is approved before coding. Each commit-sized change stops for review.

## Next

Phase 2 (per PROJECT_BRIEF.md): needs a plan, approved by the owner before coding.

## Resume here (updated 2026-11-04)

1. Check CI on GitHub → Actions for the latest push.
2. Run `rm -rf .rib` before `rib facts` on a repo cached by an older version (the cache isn't versioned; this step also adds a CHECK constraint on edge kinds, so an old `.rib/facts.sqlite` keeps the old schema).
3. Next: propose the Phase 2 plan and wait for approval.
4. Open question for the owner: version the fact cache by extractor version (proposed, not approved).

Identity check for commits: run `git config user.email` (not `--global`) inside the repo; it must show the jayaveeragh noreply address.

There is no web UI yet: the first browser view is Phase 4.
