# Status

Last updated: 2026-11-02

## Where we are

Phase 0: Foundation, done (c6d1886, e4e60a5, b47eb1a). Done criterion met: `rib fetch` prints SHA and file counts for both test repos.

Phase 1: Java facts.

| Step | State |
|---|---|
| 1. Adapter interface, Java parser (classes, methods, fields, imports), `rib facts`, answer key | done (28b0edb) |
| 2. Call resolution with confidence, interface→implementation, Spring injection; library calls listed, not traced | done (bf1eeda) |
| 3. Spring endpoints | done, awaiting owner commit (answer key: 17/17 endpoints match) |
| 4. JPA entities and repositories; check against answer key | next |

Phase 1 is done when `rib facts` on spring-petclinic @ `500158f` matches [tests/fixtures/spring-petclinic/expected.json](../tests/fixtures/spring-petclinic/expected.json).

## Working agreement

- The owner commits manually from their own account. Claude never commits and never adds a Co-Authored-By line.
- One phase at a time. A plan is approved before coding. Each commit-sized change stops for review.

## Next

Phase 1, step 4: JPA entities, relations and repositories; make edge kinds an enum; Phase 1 done check against the answer key.

## Resume here (updated 2026-11-02)

1. **Repo layout (owner decision pending):** the git repo is rooted at `~/account2`, so on GitHub everything sits under `release-impact-brief/`: the README isn't shown on the repo page and CI never runs (GitHub only reads `.github/workflows` at the root). See PROJECT_NOTES.md → Surprises.
2. Run `rm -rf .rib` before `rib facts` on a repo cached by an older version (the cache isn't versioned).
3. Next: Phase 1 step 4 (JPA entities, edge-kind enum, Phase 1 done check).
4. Open question for the owner: version the fact cache by extractor version (proposed, not approved).

There is no web UI yet: the first browser view is Phase 4.
