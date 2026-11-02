# Project notes

Working journal: assumptions, measured values, trade-offs, surprises and dead ends. Decisions themselves are in [docs/DECISIONS.md](docs/DECISIONS.md); current position is in [docs/STATUS.md](docs/STATUS.md).

Format: `YYYY-MM-DD — what — why — status (active / reversed / superseded)`

## Assumptions

- 2026-11-01 — Defects4J is mostly library code (no Spring endpoints), so it can test method-level impact only; endpoint/flow impact needs real fix commits from Spring repos — from memory, not checked — unverified, confirm in Phase 8
- 2026-11-01 — Tree-sitter has no type information, so Java call resolution is heuristic; confidence labels are what keep it honest — known limitation of syntax-only parsing — active

## Measured values

- 2026-11-01 — Call graph spring-petclinic @ `500158f`: calls 278 exact / 38 likely / 2 guess (both guesses in tests); injects 13; extends 8; main code: 93 methods, 119 library-call labels, 3 unresolved (lambda-param receiver ×2, class literal ×1) — measured with a scratch script over `extract_all` — active
- 2026-11-01 — Call graph spring-petclinic-microservices @ `e8b747a`: calls 108 exact / 16 likely / 13 guess; injects 10; implements 1 (`OwnerEntityMapper` → `Mapper`), overrides 1 (likely, generic params) — same method — active
- 2026-11-01 — Hand spot check (petclinic): `OwnerController#showOwner` → `OwnerRepository#findById` exact (petclinic declares findById itself); `PetController#processUpdateForm` → 6 exact calls incl. inherited `BaseEntity#getId`; `VisitController#loadPetWithVisit` → `getPet(petId)` with int arg linked to both 1-arg overloads as likely (true target is `getPet(Integer)`) — read source by hand — active
- 2026-11-01 — `rib facts` spring-petclinic @ `500158f`: 50 Java files, 50 classes, 195 methods, 78 fields, 278 contains-edges, 0 syntax-error files — measured with `rib facts` — active
- 2026-11-01 — `rib facts` spring-petclinic-microservices @ `e8b747a`: 64 Java files, 70 classes, 181 methods, 146 fields, 332 edges, 0 duplicate qualified names — measured with `rib facts` — active
- 2026-11-01 — spring-projects/spring-petclinic @ `500158f732419217507c7656904b8e6aa1bcc0d6`: 132 files, 1373 KB, 50 `.java` — measured with `rib fetch` — active
- 2026-11-01 — spring-petclinic/spring-petclinic-microservices @ `e8b747ab522721076cacb5cf688eb8a54ba8c5c9`: 200 files, 2512 KB, 64 `.java` — measured with `rib fetch` — active
- 2026-11-01 — Fetch limits (20,000 files, 200 MB, 1 MB per file, 120 s) are about 100x the test repos — arbitrary, never measured beyond these two repos — active

## Trade-offs and known gaps

- 2026-11-01 — Call resolution gaps (known, not handled): argument types (overloads with same argument count → `likely` to each), lambda parameters and `var` locals (type unknown), method references (`Owner::getName`), calls on class literals (`Pet.class.x()`), field initializer calls, local/anonymous classes, generic type parameters (`T`), inherited fields only one level up. Local variables are method-wide, not block-scoped (a reused name in two blocks takes the last type) — accepted for V1; all show up as `guess`/`unresolved_calls`, not as false `exact` — active
- 2026-11-01 — Total-size limit is checked after download, not during it; the 120 s timeout is the only bound on a huge download — a GitHub API size pre-check would use one of the 60 unauthenticated requests/hour — active, revisit if it matters
- 2026-11-01 — Tree-sitter over a full Java type resolver (e.g. JavaParser symbol solver) — the resolver is more accurate but needs a JVM; the brief puts compiler-based indexing after V1 — active

## Surprises

- 2026-11-01 — Tree-sitter starts a declaration's line range at its first annotation, not at the `class`/method keyword (e.g. `@Entity` on line 6, `class Owner` on line 8 → start_line 6) — found when my hand-counted test expectations were off by 2 — active; source links will include annotations, which is fine
- 2026-11-01 — For a missing or private repo, GitHub asks for credentials instead of returning 404, so git fails with "terminal prompts disabled"; mapped to "Repository not found or not public." — found while testing `rib fetch` — active
- 2026-11-01 — `uv` is not installed on the dev machine; used `venv` + `pip` instead — found at Phase 0 setup — active

## Dead ends

- 2026-11-01 — tree-sitter 0.26.0 (with tree-sitter-java 0.23.5, Python 3.14.3, macOS arm64) segfaults while extracting 3 of petclinic's test files (OwnerControllerTests, PetControllerTests, ClinicServiceTests). The crash point moves between runs (`node.text`, `named_children`, even inside unrelated imports), which looks like heap corruption in the native binding. Each API call works in isolation, so I couldn't reduce it to a small repro — not root-caused — active
- 2026-11-01 — Tried: keeping a reference to the source bytes (guess: buffer freed early) — still crashed — dead end
- 2026-11-01 — Tried: slicing our own source bytes instead of `node.text` — still crashed, so `node.text` wasn't the cause; reverted — dead end
- 2026-11-01 — Fix: tree-sitter 0.25.2 parses all 50 files cleanly, 3 runs in a row; pinned `<0.26`. A network test guards against a regression — active. Possible follow-up: report upstream with the petclinic files

## History

- 2026-11-01 — Rewrote all 4 commits' author/committer to `Jayasree Veeraghanta <153002292+jayaveeragh@users.noreply.github.com>` and force-pushed — `~/.gitconfig-account2` set only an unverifiable `@example.com` email and no name, so commits showed "mohan-veer" and counted toward no account. Old → new hashes: ac8256d→c6d1886, 88eaa5b→e4e60a5, 92a782b→b47eb1a, 49720f3→28b0edb — done by owner
