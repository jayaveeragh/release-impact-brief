# Status

Last updated: 2026-11-26

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

## Phase 3: LLM interpretation (done; plan approved 2026-11-12)

| Step | State |
|---|---|
| 1. Provider interface (Anthropic SDK + offline fake), settings from `.env`, cost tracking, spending cap; cost control can be switched off | done (cf194ff; offline tests; live test not run yet) |
| 2. Bottom-up summaries (class → module → repo) from facts + source, repo content fenced as untrusted data, cached by exact request; `rib summarize` | done (1620830; offline tests; not yet run against the real API) |
| 3. View spec schema + generation with structured output; `rib view <url>` | done (5d4c3e5) |
| 4. Validator: unknown fact references removed and logged, inferred items marked; tests that feed invented nodes | done (5d4c3e5) |

Phase 3 done criterion met on the fake provider: `rib view` writes a valid view spec, and `test_generated_view_holds_only_real_facts` feeds invented nodes and links that never survive. First real run done 2026-11-15 with the free local model (`ollama:qwen3:14b`) on spring-petclinic: valid, sensible view in ~4 min (see PROJECT_NOTES). The microservices repo hasn't been run with a real model yet.

## Phase 4: Web app overview (plan approved 2026-11-15)

| Step | State |
|---|---|
| 1. FastAPI backend: background analyses (one at a time) with stages and progress, cost confirmation for paid models, saved views; `rib serve` (127.0.0.1 only) | done (5d4c3e5) |
| 2. Frontend: React 19 + TypeScript + Vite, React Flow + ELK | done (5d4c3e5) |
| 3. Architecture diagram: nested groups, links styled by confidence, dashed outside/inferred, click for source | done (5d4c3e5) |
| 4. Panels: services, endpoints, data model, stores & outside systems, narrative with citations, flows, unclear items; GitHub links at the analysed commit | done (5d4c3e5) |

Done: reviewed and committed by the owner (5d4c3e5).

## Phase 5: Drill-down and branch map (plan approved 2026-11-15)

| Step | State |
|---|---|
| 0. GitNexus check (brief): its current docs describe opt-in per-function CFG / control dependence / def-use for all its languages, Java included — recorded in DECISIONS | done |
| 1. Module view (Level 2): summary, classes with summaries, endpoints, entities, dependencies; LLM-chosen flows on request, validated | done (d514eae) |
| 2. Java branch extraction: if / switch (cases) / ternary / catch with conditions, reads, exits, nesting; locals, calls, rejectValue | done (d514eae) |
| 3. Parameter table + branch map per endpoint, computed on click; matrix of inputs × branches; drives / read as a whole / passed through / unused | done (d514eae) |

Phase 5 done criterion met: the branch maps for three hand-checked petclinic endpoints (POST /owners/new, GET /owners, POST /owners/{ownerId}/pets/new) match the hand-written answer key — branch lines, kinds, drivers, exits, returns, throws, validation fields, the custom validator and input roles (`test_petclinic_branch_maps_match_hand_written_answer_key`).

## Phase 8: Evaluation and Python (plan approved 2026-11-26)

| Step | State |
|---|---|
| 1. Java dataset: 37 hand-picked fixes (22 Spring apps, 15 Defects4J) → 26 kept after dropping pull requests and missing issues; ground truth from the fix's diff; dev / held-out by hash | done (next commit) |
| 2. `rib eval`: issue text only, model picks locations alone, brief, scored | done (next commit) |
| 3. Results + hand analysis of misses: 18/26 hit (dev 13/16, held-out 5/10) — [eval/java/results.md](../eval/java/results.md) | done (next commit) |
| 4. Python adapter (FastAPI / Flask / Django, SQLAlchemy / Django models, branches) | not started |
| 5. BugsInPy evaluation (~10–15 bugs) | not started |

## Phase 7: Release impact brief (plan approved 2026-11-26)

| Step | State |
|---|---|
| 1. Bug input; candidate locations (retrieval shortlist, the model picks with reasons, validated); user confirms / adds by search | done (738affe) |
| 2. Deterministic traversal: callers (incl. overrides), endpoints, branches in changed methods and conditions reading the changed call's result or field, entities / relations / repositories, services calling in, flows, related tests | done (738affe) |
| 3. High / low by explainable rules; size signal with reasons; "couldn't see" list | done (738affe) |
| 4. One-page brief with cited explanation, impact overlay on the diagram, print and Markdown export | done (738affe) |

Seeded check: three petclinic bugs (owner search, future birth date, visits on the wrong pet) match the hand-written expectations (`test_seeded_petclinic_briefs_match_the_hand_written_expectations`). Done when the owner would bring a petclinic brief to a meeting — **waiting for the owner's review**.

## Phase 6: Chat (plan approved 2026-11-15)

| Step | State |
|---|---|
| 1. Retrieval: question + page context -> numbered snippets of real source (names, branch conditions, calls, summaries, synonyms; endpoint context brings handler, callees, branch map); kept read-only clone in `.rib/clones/` | done (acdd951) |
| 2. Answers: structured output, every statement cites snippet ids; uncited statements removed and listed; `not_settled` for what the code can't decide; cached | done (acdd951) |
| 3. Chat panel on the overview, module and endpoint pages; paid models ask first | done (acdd951) |

Phase 6 done criterion met: on 10 hand-written petclinic questions every statement shown cites existing lines (20 statements, 0 removed) and 10/10 answers cite the hand-marked code — `tests/test_chat_eval.py` with the local model (details in PROJECT_NOTES).

## Phase 2: System structure (done)

Plan approved 2026-11-04.

| Step | State |
|---|---|
| 1. Build and config files: Maven, Gradle (text only), Docker, Compose, Kubernetes, `application.yml` → module / config facts; `rib facts` always re-extracts | done (44ae30a; counts match hand counts on all 3 repos) |
| 2. Classify layout and runtime style with evidence; answer keys for 3 repos; create `service` facts; `rib structure` | done (da94c66; all 3 answer keys match) |
| 3. Service-to-service calls (Feign, RestTemplate/WebClient, gateway routes, messaging) and data stores | done (4ff78eb; all 3 answer keys match) |
| 4. Grey-box external systems, unclear-items list, `rib structure` | done (7834966; all 3 answer keys match) |

Phase 2 done criterion met: both petclinic repos are classified correctly with the right services (`test_structure_matches_hand_written_answer_key`), and outside systems / unresolved calls appear as `external_system` facts with `unknown` edges (`test_connections_match_hand_written_answer_key`).

## Resume here (updated 2026-11-04)

1. Check CI on GitHub → Actions for the latest push.
2. `.rib/facts.sqlite` rebuilds itself when the schema changes, and `rib` re-extracts every run: no more `rm -rf .rib`.
3. Next: owner commits Phase 8a (Java evaluation); then Phase 8b, the Python adapter and BugsInPy evaluation.

Identity check for commits: run `git config user.email` (not `--global`) inside the repo; it must show the jayaveeragh noreply address.

There is no web UI yet: the first browser view is Phase 4.
