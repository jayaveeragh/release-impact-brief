import subprocess
from pathlib import Path

import pytest

from release_impact_brief.clones import ensure_clone, read_lines
from release_impact_brief.fetch import Limits

LOCAL = ("file",)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()  # fmt: skip


@pytest.fixture
def remote(tmp_path: Path) -> tuple[str, str]:
    src = tmp_path / "src"
    (src / "app").mkdir(parents=True)
    _git(src, "init", "--quiet", "-b", "main")
    (src / "app" / "App.java").write_text("class App {\n  int a;\n  int b;\n}\n")
    _git(src, "add", ".")
    _git(src, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "init")
    return src.as_uri(), _git(src, "rev-parse", "HEAD")


def test_clone_is_made_once_and_kept(tmp_path: Path, remote: tuple[str, str]) -> None:
    url, sha = remote
    root = tmp_path / "clones"
    first = ensure_clone(url, sha, root, Limits(), LOCAL)
    assert (first / "app" / "App.java").is_file()
    (first / "app" / "marker").write_text("kept")
    second = ensure_clone(url, sha, root, Limits(), LOCAL)
    assert second == first and (second / "app" / "marker").is_file()  # not cloned again


def test_failed_clone_leaves_nothing_half_made(tmp_path: Path, remote: tuple[str, str]) -> None:
    url, sha = remote
    root = tmp_path / "clones"
    from release_impact_brief.fetch import FetchError

    with pytest.raises(FetchError):
        ensure_clone(url, sha, root, Limits(max_total_bytes=1), LOCAL)  # too big
    assert not any(root.iterdir())


def test_read_lines_stays_inside_the_clone(tmp_path: Path, remote: tuple[str, str]) -> None:
    url, sha = remote
    clone = ensure_clone(url, sha, tmp_path / "clones", Limits(), LOCAL)
    assert read_lines(clone, "app/App.java", 2, 3) == ["  int a;", "  int b;"]
    assert read_lines(clone, "app/App.java", 4, 99) == ["}"]
    for bad in ("../../etc/passwd", "/etc/passwd", "app/missing.java"):
        assert read_lines(clone, bad, 1, 2) == []
