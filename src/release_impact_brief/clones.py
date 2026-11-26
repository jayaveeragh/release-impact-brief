"""Kept checkouts, for reading code snippets when answering questions.

Made with the same locked-down clone as every fetch (one pinned commit, no hooks,
no submodules, symlinks as plain files) and the same limits, then kept under
`.rib/clones/` (gitignored) instead of deleted. Nothing in them is ever run; only
lines of text are read, and only from inside the clone.
"""

import hashlib
import shutil
import threading
from pathlib import Path

from release_impact_brief.fetch import Limits, clone_at, load_limits, scan

_DONE = ".rib-complete"
_lock = threading.Lock()  # two questions at once must not clone the same repo twice


def ensure_clone(repo_url: str, sha: str, root: Path, limits: Limits | None = None,
                 protocols: tuple[str, ...] = ("https",)) -> Path:  # fmt: skip
    """The checkout of repo_url@sha under root, cloning it the first time."""
    home = root / f"{hashlib.sha256(repo_url.encode()).hexdigest()[:12]}@{sha[:12]}"
    checkout = home / "repo"
    with _lock:
        if (home / _DONE).is_file():
            return checkout
        shutil.rmtree(home, ignore_errors=True)
        home.mkdir(parents=True)
        try:
            limits = limits or load_limits()
            clone_at(repo_url, sha, checkout, limits, protocols)
            scan(checkout, limits)
            (home / _DONE).write_text(sha)
        except BaseException:
            shutil.rmtree(home, ignore_errors=True)
            raise
    return checkout


def read_lines(checkout: Path, file: str, start: int, end: int) -> list[str]:
    """Lines start..end (1-based, inclusive) of a file inside the checkout; [] otherwise."""
    path = (checkout / file).resolve()
    if not path.is_relative_to(checkout.resolve()) or not path.is_file():
        return []
    lines = path.read_text("utf-8", "replace").splitlines()
    return lines[max(start, 1) - 1 : end]
