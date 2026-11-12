# Status

Last updated: 2026-11-11

## Where we are

Phase 0: Foundation, done (c6d1886, e4e60a5, b47eb1a). Done criterion met: `rib fetch` prints SHA and file counts for both test repos.

Phase 1: Java facts.

| Step | State |
|---|---|
| 1. Adapter interface, Java parser (classes, methods, fields, imports), `rib facts`, answer key | done (28b0edb) |
| 2. Call resolution with confidence, interface→implementation, Spring injection; library calls listed, not traced | done (bf1eeda) |
| 3. Spring endpoints | done (41abb08, together with the move to repo root; answer key: 17/17 endpoints match) |
| 4. JPA entities, relations and repositories; edge kinds as an enum; check against answer key | done (394f009; answer key: entities, mapped superclasses, relations, repositories all match) |
| 5. Known gaps marked, not guessed: Lombok-generated getters/setters labelled | done (44ae30a) |

Phase 1 done criterion met: `rib facts` on spring-petclinic @ `500158f` matches [tests/fixtures/spring-petclinic/expected.json](../tests/fixtures/spring-petclinic/expected.json) (network tests `test_petclinic_endpoints_match_hand_written_answer_key` and `test_petclinic_data_model_matches_hand_written_answer_key`).

## Working agreement

- The owner commits manually from their own account. Claude never commits and never adds a Co-Authored-By line.
- One phase at a time. A plan is approved before coding. Each commit-sized change stops for review.

## Next

Phase 2: System structure (plan approved 2026-11-04).

| Step | State |
|---|---|
| 1. Build and config files: Maven, Gradle (text only), Docker, Compose, Kubernetes, `application.yml` → module / config facts; `rib facts` always re-extracts | done (44ae30a; counts match hand counts on all 3 repos) |
| 2. Classify layout and runtime style with evidence; answer keys for 3 repos; create `service` facts; `rib structure` | done (da94c66; all 3 answer keys match) |
| 3. Service-to-service calls (Feign, RestTemplate/WebClient, gateway routes, messaging) and data stores | done (next commit; all 3 answer keys match) |
| 4. Grey-box external systems, unclear-items list, `rib structure` | next |

Done when: both petclinic repos are classified correctly, with the right services, and unresolved calls appear as unknowns.

## Resume here (updated 2026-11-04)

1. Check CI on GitHub → Actions for the latest push.
2. `.rib/facts.sqlite` rebuilds itself when the schema changes, and `rib` re-extracts every run: no more `rm -rf .rib`.
3. Next: Phase 2 step 4: turn `unresolved_targets` and outside systems seen in config (e.g. mall's Alipay, MinIO, Aliyun OSS, Logstash) into grey-box `external_system` facts, build the unclear-items list (unresolved targets, `guess` edges, "unclear" verdicts) and print it in `rib structure`. Then the Phase 2 done check.

Identity check for commits: run `git config user.email` (not `--global`) inside the repo; it must show the jayaveeragh noreply address.

There is no web UI yet: the first browser view is Phase 4.
