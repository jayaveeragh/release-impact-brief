import subprocess
from pathlib import Path

import pytest

from release_impact_brief.cli import main
from release_impact_brief.fetch import (
    FetchError,
    Limits,
    RepoRef,
    clone_at,
    fetch_repo,
    parse_github_url,
    resolve_sha,
    scan,
)

LOCAL = ("file",)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/spring-projects/spring-petclinic",
        "https://github.com/spring-projects/spring-petclinic/",
        "https://github.com/spring-projects/spring-petclinic.git",
        "  https://www.github.com/spring-projects/spring-petclinic  ",
    ],
)
def test_accepts_plain_github_repo_urls(url: str) -> None:
    assert parse_github_url(url) == RepoRef("spring-projects", "spring-petclinic")


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b",
        "git@github.com:a/b.git",
        "file:///etc/passwd",
        "https://gitlab.com/a/b",
        "https://github.com.evil.com/a/b",
        "https://user:token@github.com/a/b",
        "https://github.com:8443/a/b",
        "https://127.0.0.1/a/b",
        "https://github.com/a",
        "https://github.com/a/b/tree/main",
        "https://github.com/a/b?x=1",
        "https://github.com/-a/b",
        "https://github.com/a/..",
        "",
    ],
)
def test_rejects_other_urls(url: str) -> None:
    with pytest.raises(FetchError):
        parse_github_url(url)


def test_rejects_ref_that_looks_like_an_option() -> None:
    with pytest.raises(FetchError):
        resolve_sha("https://github.com/a/b", "--upload-pack=evil", Limits())


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def source_repo(tmp_path: Path) -> tuple[str, str]:
    """A local repo with a symlink pointing outside it and a declared submodule."""
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "--quiet", "-b", "main")
    (src / "App.java").write_text("class App {}\n")
    (src / "README.md").write_text("hello\n")
    (src / "escape").symlink_to("/etc/passwd")
    (src / ".gitmodules").write_text('[submodule "x"]\n\tpath = x\n\turl = https://example.com/x\n')
    _git(src, "add", ".")
    _git(src, "commit", "--quiet", "-m", "init")
    _git(src, "tag", "v1")
    return src.as_uri(), _git(src, "rev-parse", "HEAD")


def test_resolves_head_branch_and_tag(source_repo: tuple[str, str]) -> None:
    remote, sha = source_repo
    for ref in (None, "main", "v1", sha):
        assert resolve_sha(remote, ref, Limits(), LOCAL) == sha


def test_unknown_ref_is_a_clear_error(source_repo: tuple[str, str]) -> None:
    remote, _ = source_repo
    with pytest.raises(FetchError, match="not found"):
        resolve_sha(remote, "no-such-branch", Limits(), LOCAL)


def test_file_protocol_is_blocked_by_default(source_repo: tuple[str, str]) -> None:
    remote, _ = source_repo
    with pytest.raises(FetchError):
        resolve_sha(remote, None, Limits())


def test_clone_pins_commit_and_neutralises_symlinks(
    source_repo: tuple[str, str], tmp_path: Path
) -> None:
    remote, sha = source_repo
    dest = tmp_path / "clone"
    clone_at(remote, sha, dest, Limits(), LOCAL)

    assert _git(dest, "rev-parse", "HEAD") == sha
    escape = dest / "escape"
    assert not escape.is_symlink()
    assert escape.read_text() == "/etc/passwd"
    assert not (dest / "x").exists()

    count, _, by_ext, skipped = scan(dest, Limits())
    assert count == 4
    assert by_ext[".java"] == 1
    assert skipped == []


def test_scan_refuses_symlinks(tmp_path: Path) -> None:
    (tmp_path / "link").symlink_to("/etc/passwd")
    with pytest.raises(FetchError, match="symlink"):
        scan(tmp_path, Limits())


def test_scan_enforces_file_count_limit(tmp_path: Path) -> None:
    for i in range(3):
        (tmp_path / f"f{i}.txt").write_text("x")
    with pytest.raises(FetchError, match="more than 2 files"):
        scan(tmp_path, Limits(max_files=2))


def test_scan_enforces_total_size_limit(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"x" * 600)
    (tmp_path / "b.txt").write_bytes(b"x" * 600)
    with pytest.raises(FetchError, match="limit exceeded"):
        scan(tmp_path, Limits(max_total_bytes=1000))


def test_scan_skips_files_over_size_limit(tmp_path: Path) -> None:
    (tmp_path / "small.txt").write_bytes(b"x" * 10)
    (tmp_path / "big.bin").write_bytes(b"x" * 100)
    count, total, _, skipped = scan(tmp_path, Limits(max_file_bytes=50))
    assert (count, total, skipped) == (1, 10, ["big.bin"])


def test_cli_reports_bad_url(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["fetch", "https://gitlab.com/a/b"]) == 1
    assert "error:" in capsys.readouterr().err


@pytest.mark.network
def test_fetch_real_repo_and_clean_up() -> None:
    with fetch_repo("https://github.com/spring-projects/spring-petclinic") as result:
        path = result.path
        assert len(result.commit_sha) == 40
        assert result.by_extension[".java"] > 10
    assert not path.exists()
