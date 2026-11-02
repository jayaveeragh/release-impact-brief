"""Safe fetch of a public GitHub repo at one pinned commit.

Repo content is untrusted: nothing from it is built, installed or executed.
Git runs with user/system config ignored, https only, no submodules, no hooks
and symlinks checked out as plain files, so the clone cannot point outside itself.
"""

import os
import re
import subprocess
import tempfile
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_REPO_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
_REF_RE = re.compile(r"[A-Za-z0-9._/-]{1,200}")
_SHA_RE = re.compile(r"[0-9a-f]{40}")


class FetchError(Exception):
    """A fetch was refused or failed; the message is safe to show the user."""


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"


@dataclass(frozen=True)
class Limits:
    max_files: int = 20_000
    max_total_bytes: int = 200 * 1024 * 1024
    max_file_bytes: int = 1024 * 1024
    timeout_seconds: int = 120


@dataclass(frozen=True)
class FetchResult:
    repo: RepoRef
    commit_sha: str
    path: Path
    file_count: int
    total_bytes: int
    by_extension: Counter[str]
    skipped_large: list[str] = field(default_factory=list)


def parse_github_url(url: str) -> RepoRef:
    """Accept only https://github.com/<owner>/<repo>, optionally with .git or a trailing /."""
    parts = urlsplit(url.strip())
    if parts.scheme != "https":
        raise FetchError("Only https:// GitHub URLs are accepted.")
    if parts.netloc.lower() not in ("github.com", "www.github.com"):
        raise FetchError("Only github.com URLs are accepted (no other hosts, ports or logins).")
    if parts.query or parts.fragment:
        raise FetchError("URL must not contain a query string or fragment.")
    segments = parts.path.strip("/").split("/")
    if len(segments) != 2:
        raise FetchError("URL must look like https://github.com/<owner>/<repo>.")
    owner, name = segments
    name = name.removesuffix(".git")
    if not _OWNER_RE.fullmatch(owner) or not _REPO_RE.fullmatch(name) or name in (".", ".."):
        raise FetchError("Invalid owner or repository name in URL.")
    return RepoRef(owner, name)


def _git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_LFS_SKIP_SMUDGE="1",
    )
    return env


def _git(args: Sequence[str], cwd: Path | None, limits: Limits, protocols: Sequence[str]) -> str:
    hardening = [
        "-c", "protocol.allow=never",
        *(arg for p in protocols for arg in ("-c", f"protocol.{p}.allow=always")),
        "-c", "core.symlinks=false",
        "-c", f"core.hooksPath={os.devnull}",
        "-c", "core.fsmonitor=false",
        "-c", "submodule.recurse=false",
        "-c", "fetch.recurseSubmodules=false",
        "-c", "advice.detachedHead=false",
    ]  # fmt: skip
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", *hardening, *args],  # noqa: S607 - git resolved from PATH
            cwd=cwd,
            env=_git_env(),
            capture_output=True,
            text=True,
            timeout=limits.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FetchError(f"git timed out after {limits.timeout_seconds}s.") from exc
    if proc.returncode != 0:
        if "terminal prompts disabled" in proc.stderr:
            # GitHub asks for credentials when a repo is missing or private.
            raise FetchError("Repository not found or not public.")
        raise FetchError(f"git {args[0]} failed: {proc.stderr.strip()[:500]}")
    return proc.stdout


def resolve_sha(
    remote: str, ref: str | None, limits: Limits, protocols: Sequence[str] = ("https",)
) -> str:
    """Turn a branch, tag or SHA (default: the remote's HEAD) into a full commit SHA."""
    if ref is not None and _SHA_RE.fullmatch(ref):
        return ref
    if ref is not None and (not _REF_RE.fullmatch(ref) or ref.startswith("-")):
        raise FetchError("Invalid ref name.")
    out = _git(["ls-remote", "--", remote, ref or "HEAD"], None, limits, protocols)
    refs: dict[str, str] = {}
    for line in out.splitlines():
        sha, _, name = line.partition("\t")
        refs[name] = sha
    wanted = (
        ["HEAD"]
        if ref is None
        else [f"refs/tags/{ref}^{{}}", f"refs/tags/{ref}", f"refs/heads/{ref}"]
    )
    for name in wanted:
        if name in refs:
            return refs[name]
    raise FetchError(f"Ref {ref or 'HEAD'!r} not found (is the repo public and does it exist?).")


def clone_at(
    remote: str, sha: str, dest: Path, limits: Limits, protocols: Sequence[str] = ("https",)
) -> None:
    """Fetch exactly one commit into dest, with no history beyond it and no submodules."""
    _git(["init", "--quiet", str(dest)], None, limits, protocols)
    _git(["remote", "add", "origin", remote], dest, limits, protocols)
    _git(
        ["fetch", "--quiet", "--depth", "1", "--no-recurse-submodules", "origin", sha],
        dest,
        limits,
        protocols,
    )
    _git(["checkout", "--quiet", "FETCH_HEAD"], dest, limits, protocols)


def scan(root: Path, limits: Limits) -> tuple[int, int, Counter[str], list[str]]:
    """Count files under root, enforcing limits. Never follows symlinks."""
    file_count = 0
    total_bytes = 0
    by_extension: Counter[str] = Counter()
    skipped_large: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fname in filenames:
            path = Path(dirpath, fname)
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise FetchError(f"Refusing symlink in clone: {rel}")
            size = path.stat().st_size
            if size > limits.max_file_bytes:
                skipped_large.append(rel)
                continue
            file_count += 1
            total_bytes += size
            if file_count > limits.max_files:
                raise FetchError(f"Repo has more than {limits.max_files} files; limit exceeded.")
            if total_bytes > limits.max_total_bytes:
                raise FetchError(
                    f"Repo is larger than {limits.max_total_bytes // (1024 * 1024)} MB; "
                    "limit exceeded."
                )
            by_extension[path.suffix.lower() or "(none)"] += 1
    return file_count, total_bytes, by_extension, skipped_large


@contextmanager
def fetch_repo(
    url: str, ref: str | None = None, limits: Limits | None = None
) -> Iterator[FetchResult]:
    """Fetch a public GitHub repo at a pinned commit; the clone is deleted on exit."""
    limits = limits or Limits()
    repo = parse_github_url(url)
    sha = resolve_sha(repo.url, ref, limits)
    with tempfile.TemporaryDirectory(prefix="rib-") as tmp:
        dest = Path(tmp, "repo")
        clone_at(repo.url, sha, dest, limits)
        file_count, total_bytes, by_extension, skipped = scan(dest, limits)
        yield FetchResult(repo, sha, dest, file_count, total_bytes, by_extension, skipped)
