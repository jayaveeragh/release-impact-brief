# release-impact-brief

> Work in progress. Phase 0 (foundation).

Paste a public GitHub URL and get a visual explanation of the repository, from architecture down to the branches inside one endpoint. Describe a bug and get a one-page brief of what a likely fix could affect, to help decide whether the fix goes into a release.

Design rule: **facts first, creativity on top.** Deterministic parsing produces facts with file, line and confidence. An LLM decides how to present them. A validator drops anything the facts don't support.

## Development

Requires Python 3.12+.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest
```

## Docs

- [docs/STATUS.md](docs/STATUS.md): where the project is and what's next
- [docs/DECISIONS.md](docs/DECISIONS.md): decisions and why

## License

MIT, see [LICENSE](LICENSE).
