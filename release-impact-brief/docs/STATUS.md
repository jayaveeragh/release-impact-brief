# Status

Last updated: 2026-11-01

## Where we are

Phase 0: Foundation, done (ac8256d, 88eaa5b, 92a782b). Done criterion met: `rib fetch` prints SHA and file counts for both test repos.

Phase 1: Java facts.

| Step | State |
|---|---|
| 1. Adapter interface, Java parser (classes, methods, fields, imports), `rib facts`, answer key | done, awaiting owner commit |
| 2. Call resolution with confidence, interface→implementation, Spring injection; library calls listed, not traced | next |
| 3. Spring endpoints | not started |
| 4. JPA entities and repositories; check against answer key | not started |

Phase 1 is done when `rib facts` on spring-petclinic @ `500158f` matches [tests/fixtures/spring-petclinic/expected.json](../tests/fixtures/spring-petclinic/expected.json).

## Working agreement

- The owner commits manually from their own account. Claude never commits and never adds a Co-Authored-By line.
- One phase at a time. A plan is approved before coding. Each commit-sized change stops for review.

## Next

Phase 1, step 2: call edges between user-defined code, with `exact` / `likely` / `guess` confidence.
