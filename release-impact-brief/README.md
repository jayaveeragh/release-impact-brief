# release-impact-brief

> Work in progress. Phase 0 (foundation) done; Phase 1 (Java facts) in progress.

Paste a public GitHub URL and get a visual explanation of the repository, from architecture down to the branches inside one endpoint. Describe a bug and get a one-page brief of what a likely fix could affect, to help decide whether the fix goes into a release.

Design rule: **facts first, creativity on top.** Deterministic parsing produces facts with file, line and confidence. An LLM decides how to present them. A validator drops anything the facts don't support.

## Progress

| Phase | Commit | What it added |
|---|---|---|
| 0 | `c6d1886` | Project skeleton: lint (ruff), strict types (mypy), tests (pytest), CI, MIT license, docs |
| 0 | `e4e60a5` | Fact store: SQLite nodes and edges keyed by repo + commit SHA, with confidence labels (`exact`, `likely`, `guess`, `unknown`) |
| 0 | `b47eb1a` | Safe fetch: strict GitHub URL check, one pinned commit cloned with locked-down git, size limits, automatic cleanup, `rib fetch` CLI |
| 1 | `28b0edb` | Java parser (Tree-sitter): classes, interfaces, enums, records, methods, fields, imports with file and line; `rib facts` CLI cached by commit SHA; hand-written answer key for spring-petclinic. Also adds PROJECT_NOTES.md |
| 1 | _next commit_ | Call graph: `calls` edges between your own code with `exact` / `likely` / `guess` confidence; `extends`, `implements`, `overrides`; Spring `injects` (constructor, `@Autowired`, Lombok). Library calls (JDK, Spring) are listed on the method, never traced; unresolvable calls are listed, never guessed |

## Development

Requires Python 3.12+.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest              # offline tests
.venv/bin/pytest -m network   # tests that fetch from GitHub
```

## Usage

```bash
.venv/bin/rib fetch https://github.com/spring-projects/spring-petclinic [--ref <branch|tag|sha>]
```

This fetches one pinned commit into a temp folder (nothing from the repo is executed), prints the commit SHA and file counts, then deletes the clone.

```bash
.venv/bin/rib facts https://github.com/spring-projects/spring-petclinic [--kind class|method|field|...]
```

This extracts code facts (each with file and line), stores them in `.rib/facts.sqlite` keyed by commit SHA, and lists them. A second run for the same commit uses the cache.

## Docs

- [docs/STATUS.md](docs/STATUS.md): where the project is and what's next
- [docs/DECISIONS.md](docs/DECISIONS.md): decisions and why
- [PROJECT_NOTES.md](PROJECT_NOTES.md): assumptions, measurements, surprises and dead ends

## License

MIT, see [LICENSE](LICENSE).
