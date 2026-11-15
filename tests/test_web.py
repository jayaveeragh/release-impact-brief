import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from release_impact_brief.fetch import FetchResult, RepoRef
from release_impact_brief.llm import FakeProvider, Provider, Settings
from release_impact_brief.web.app import create_app
from release_impact_brief.web.jobs import JobRunner

POM = "<project><groupId>shop</groupId><artifactId>shop</artifactId></project>"
FILES = {
    "pom.xml": POM,
    "src/main/java/shop/ShopApp.java": "package shop;\n@SpringBootApplication\nclass ShopApp {}\n",
    "src/main/java/shop/Hello.java": """\
package shop;

@RestController
class Hello {
    @GetMapping("/hello")
    public String hello() { return "hi"; }
}
""",
}
SHA = "c" * 40


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def fetcher(root: Path) -> Any:
    @contextmanager
    def fetch(url: str, ref: str | None) -> Iterator[FetchResult]:
        from collections import Counter

        yield FetchResult(RepoRef("acme", "shop"), SHA, root, 3, 100, Counter(), [])

    return fetch


def client(tmp_path: Path, repo: Path, model: str = "fake",
           provider: Provider | None = None) -> TestClient:  # fmt: skip
    settings = Settings(model=model, effort="medium", cost_control=True, spend_cap_usd=1.0,
                        concurrency=1)  # fmt: skip
    runner = JobRunner(tmp_path / ".rib" / "facts.sqlite", fetch=fetcher(repo),
                       settings=lambda: settings,
                       provider=lambda s: provider or FakeProvider(model=s.model))  # fmt: skip
    return TestClient(create_app(runner, static_dir=None))


def wait(c: TestClient, job_id: str, until: set[str]) -> dict[str, Any]:
    for _ in range(200):
        job: dict[str, Any] = c.get(f"/api/analyses/{job_id}").json()
        if job["status"] in until:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job stuck in {job['status']}")


def test_bad_url_is_rejected_before_any_work(tmp_path: Path, repo: Path) -> None:
    c = client(tmp_path, repo)
    response = c.post("/api/analyses", json={"url": "https://gitlab.com/a/b"})
    assert response.status_code == 400
    assert "github.com" in response.json()["detail"]


def test_analysis_runs_to_a_saved_view(tmp_path: Path, repo: Path) -> None:
    c = client(tmp_path, repo)
    job = c.post("/api/analyses", json={"url": "https://github.com/acme/shop"}).json()
    assert job["status"] in ("queued", "fetching", "extracting", "summarizing", "viewing", "done")
    done = wait(c, job["id"], {"done", "failed"})
    assert done["status"] == "done", done
    assert done["view"] == f"acme__shop@{SHA[:12]}"
    assert done["total"] == done["completed"] > 0

    listed = c.get("/api/views").json()
    assert [v["name"] for v in listed] == [done["view"]]
    assert listed[0]["repo"] == "https://github.com/acme/shop"
    view = c.get(f"/api/views/{done['view']}").json()
    assert view["commit"] == SHA
    assert [e["path"] for e in view["panels"]["endpoints"]] == ["/hello"]
    assert view["runtime_style"]["value"] == "monolith"


def test_paid_model_waits_for_confirmation(tmp_path: Path, repo: Path) -> None:
    c = client(tmp_path, repo, model="claude-opus-5-5",
               provider=FakeProvider(model="claude-opus-5-5"))  # fmt: skip
    job = c.post("/api/analyses", json={"url": "https://github.com/acme/shop"}).json()
    waiting = wait(c, job["id"], {"awaiting_confirmation", "done", "failed"})
    assert waiting["status"] == "awaiting_confirmation"
    assert 0 < waiting["estimate"]["min_usd"] <= waiting["estimate"]["max_usd"]
    assert c.post(f"/api/analyses/{job['id']}/confirm").status_code == 200
    assert wait(c, job["id"], {"done", "failed"})["status"] == "done"


def test_cancelled_confirmation_spends_nothing(tmp_path: Path, repo: Path) -> None:
    provider = FakeProvider(model="claude-opus-5-5")
    c = client(tmp_path, repo, model="claude-opus-5-5", provider=provider)
    job = c.post("/api/analyses", json={"url": "https://github.com/acme/shop"}).json()
    wait(c, job["id"], {"awaiting_confirmation"})
    c.post(f"/api/analyses/{job['id']}/cancel")
    stopped = wait(c, job["id"], {"cancelled", "failed", "done"})
    assert stopped["status"] == "cancelled" and provider.calls == []


def test_view_names_cannot_escape_the_views_folder(tmp_path: Path, repo: Path) -> None:
    c = client(tmp_path, repo)
    secret = tmp_path / ".rib" / "secret.json"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text(json.dumps({"x": 1}))
    for name in ("..__secret@000000000000", "../secret", "acme__shop@zzz"):
        assert c.get(f"/api/views/{name}").status_code == 404
    assert c.get("/api/analyses/nope").status_code == 404


def analysed(tmp_path: Path, repo: Path, **kwargs: Any) -> tuple[TestClient, str]:
    c = client(tmp_path, repo, **kwargs)
    job = c.post("/api/analyses", json={"url": "https://github.com/acme/shop"}).json()
    done = wait(c, job["id"], {"done", "failed"})
    assert done["status"] == "done", done
    return c, str(done["view"])


def test_branch_map_is_computed_on_demand(tmp_path: Path, repo: Path) -> None:
    c, view = analysed(tmp_path, repo)
    result = c.get(f"/api/views/{view}/branches", params={"endpoint": "java:endpoint:GET /hello"})
    assert result.status_code == 200
    body = result.json()
    assert body["handler"] == "shop.Hello#hello()" and body["returns"] == [6]
    missing = c.get(f"/api/views/{view}/branches", params={"endpoint": "java:endpoint:GET /x"})
    assert missing.status_code == 404


def test_module_flows_on_demand(tmp_path: Path, repo: Path) -> None:
    c, view = analysed(tmp_path, repo)
    result = c.post(f"/api/views/{view}/flows", json={"module": "build:module:."})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["module"] == "build:module:." and body["flows"] == []  # fake model: no flows
    assert (
        c.post(f"/api/views/{view}/flows", json={"module": "build:module:nope"}).status_code == 404
    )


def test_paid_module_flows_ask_first(tmp_path: Path, repo: Path) -> None:
    provider = FakeProvider(model="claude-opus-5-5")
    c, view = analysed(tmp_path, repo)  # analysed for free first
    c.app.state.runner._provider = lambda s: provider  # type: ignore[attr-defined]
    c.app.state.runner._settings = lambda: Settings("claude-opus-5-5", "medium", True, 1.0, 1)  # type: ignore[attr-defined]
    first = c.post(f"/api/views/{view}/flows", json={"module": "build:module:."}).json()
    assert first["needs_confirmation"] is True and first["estimate"]["max_usd"] > 0
    assert provider.calls == []
    second = c.post(f"/api/views/{view}/flows", json={"module": "build:module:.", "confirm": True})
    assert second.status_code == 200 and "flows" in second.json() and len(provider.calls) == 1
