"""HTTP API for the web app (local only: `rib serve` binds 127.0.0.1).

POST /api/analyses {url, ref?}       start an analysis -> job
GET  /api/analyses/{id}              stage, progress, cost estimate, error
POST /api/analyses/{id}/confirm      go ahead with a paid run
POST /api/analyses/{id}/cancel       don't
GET  /api/views                      saved views
GET  /api/views/{name}               one view: spec + fact panels
GET  /api/views/{name}/branches?endpoint=<id>   branch map of one endpoint (facts only)
POST /api/views/{name}/flows {module, confirm?} LLM-chosen flows of one module, validated;
                                     a paid model first answers with the estimate
POST /api/views/{name}/chat {question, context, confirm?}  answer from cited code (read-only)
GET  /api/views/{name}/search?q=     facts to add as impact locations, by name
POST /api/views/{name}/impact/candidates {description, area?, fix?, confirm?}
POST /api/views/{name}/impact/brief {description, area?, fix?, locations, confirm?}

No secrets are ever returned: settings stay on the server.
"""

import json
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from release_impact_brief.adapters import Extraction
from release_impact_brief.branches import branch_map
from release_impact_brief.chat import ask, chat_request
from release_impact_brief.clones import ensure_clone
from release_impact_brief.fetch import FetchError
from release_impact_brief.impact import (
    analyze_impact,
    bug_text,
    candidates_request,
    explain,
    explain_request,
    pick_candidates,
    search_facts,
    to_markdown,
)
from release_impact_brief.llm import (
    CostUnknown,
    LlmAuthError,
    LlmError,
    LlmRequest,
    LlmSession,
    LlmUnavailable,
    SpendingCapExceeded,
)
from release_impact_brief.summaries import SummaryCache
from release_impact_brief.views import flows_request, generate_module_flows
from release_impact_brief.web.jobs import JobRunner

_LLM_ERRORS = (LlmAuthError, LlmUnavailable, LlmError, SpendingCapExceeded, CostUnknown)

# owner__repo@<12 hex>: no slashes or leading dots, so a name can't leave the views folder
_VIEW_NAME = re.compile(r"^[A-Za-z0-9][\w.-]*__[A-Za-z0-9][\w.-]*@[0-9a-f]{12}$")


class AnalysisIn(BaseModel):
    url: str
    ref: str | None = None


class FlowsIn(BaseModel):
    module: str
    confirm: bool = False


class BugIn(BaseModel):
    description: str
    area: str = ""
    fix: str = ""
    locations: list[str] = []
    confirm: bool = False


class ChatIn(BaseModel):
    question: str
    context: dict[str, str] = {"kind": "repo"}
    confirm: bool = False


def create_app(runner: JobRunner, static_dir: Path | None) -> FastAPI:
    app = FastAPI(title="release-impact-brief", docs_url=None, redoc_url=None)
    app.state.runner = runner

    def job_or_404(job_id: str) -> dict[str, Any]:
        job = runner.get(job_id)
        if job is None:
            raise HTTPException(404, "no such analysis")
        return job.to_dict()

    @app.post("/api/analyses", status_code=202)
    def start(body: AnalysisIn) -> dict[str, Any]:
        try:
            return runner.start(body.url.strip(), body.ref).to_dict()
        except FetchError as error:
            raise HTTPException(400, str(error)) from None

    @app.get("/api/analyses/{job_id}")
    def status(job_id: str) -> dict[str, Any]:
        return job_or_404(job_id)

    @app.post("/api/analyses/{job_id}/confirm")
    def confirm(job_id: str) -> dict[str, Any]:
        if not runner.decide(job_id, True):
            raise HTTPException(409, "this analysis isn't waiting for confirmation")
        return job_or_404(job_id)

    @app.post("/api/analyses/{job_id}/cancel")
    def cancel(job_id: str) -> dict[str, Any]:
        if not runner.decide(job_id, False):
            raise HTTPException(409, "this analysis isn't waiting for confirmation")
        return job_or_404(job_id)

    @app.get("/api/views")
    def views() -> list[dict[str, Any]]:
        found = []
        for path in sorted(runner.views_dir.glob("*.json"), key=lambda p: -p.stat().st_mtime):
            if not _VIEW_NAME.match(path.stem):
                continue
            view = json.loads(path.read_text())
            found.append({"name": path.stem, "repo": view["repo"], "commit": view["commit"],
                          "model": view["model"], "runtime": view["runtime_style"]["value"],
                          "layout": view["repo_layout"]["value"]})  # fmt: skip
        return found

    def load(name: str) -> dict[str, Any]:
        path = runner.views_dir / f"{name}.json"
        if not _VIEW_NAME.match(name) or not path.is_file():
            raise HTTPException(404, "no such view")
        return dict(json.loads(path.read_text()))

    def facts_of(view: dict[str, Any]) -> Extraction:
        facts = runner.facts(view["repo"], view["commit"])
        if facts is None:
            raise HTTPException(404, "the facts for this view are gone: analyse the repo again")
        return facts

    @app.get("/api/views/{name}")
    def view(name: str) -> dict[str, Any]:
        return load(name)

    @app.get("/api/views/{name}/branches")
    def branches(name: str, endpoint: str) -> dict[str, Any]:
        facts = facts_of(load(name))
        try:
            return branch_map(facts.nodes, facts.edges, endpoint)
        except KeyError:
            raise HTTPException(404, "no such endpoint") from None

    @app.post("/api/views/{name}/flows")
    def flows(name: str, body: FlowsIn) -> dict[str, Any]:
        view = load(name)
        module = next((m for m in view["panels"]["modules"] if m["id"] == body.module), None)
        if module is None:
            raise HTTPException(404, "no such module")
        facts = facts_of(view)
        session = runner.session()
        request, _ = flows_request(facts, body.module, module, view["repo"])
        if session.settings.cost_control and not body.confirm:
            estimate = session.estimate([request])
            if (estimate.max_usd or 0) > 0:
                return {"needs_confirmation": True, "estimate": {
                    "min_usd": estimate.min_usd, "max_usd": estimate.max_usd,
                    "cap_usd": session.settings.spend_cap_usd}}  # fmt: skip
        cache = SummaryCache(runner.db)
        try:
            return generate_module_flows(facts, body.module, module, view["repo"],
                                         view["commit"], session, cache)  # fmt: skip
        except (LlmAuthError, LlmUnavailable, LlmError, SpendingCapExceeded, CostUnknown) as e:
            raise HTTPException(503, str(e)) from None
        finally:
            cache.close()

    @app.post("/api/views/{name}/chat")
    def chat(name: str, body: ChatIn) -> dict[str, Any]:
        question = body.question.strip()
        if not question or len(question) > 2000:
            raise HTTPException(400, "ask a question (up to 2000 characters)")
        view = load(name)
        facts = facts_of(view)
        try:
            checkout = ensure_clone(view["repo"], view["commit"], runner.db.parent / "clones")
        except FetchError as error:
            raise HTTPException(503, str(error)) from None
        session = runner.session()
        if session.settings.cost_control and not body.confirm:
            request, _ = chat_request(facts, view, checkout, question, body.context)
            estimate = session.estimate([request])
            if (estimate.max_usd or 0) > 0:
                return {"needs_confirmation": True, "estimate": {
                    "min_usd": estimate.min_usd, "max_usd": estimate.max_usd,
                    "cap_usd": session.settings.spend_cap_usd}}  # fmt: skip
        cache = SummaryCache(runner.db)
        try:
            return ask(facts, view, checkout, question, body.context, session, cache)
        except (LlmAuthError, LlmUnavailable, LlmError, SpendingCapExceeded, CostUnknown) as e:
            raise HTTPException(503, str(e)) from None
        finally:
            cache.close()

    def ask_first(session: LlmSession, request: LlmRequest, confirm: bool) -> dict[str, Any] | None:
        """With a paid model and cost control on, the estimate instead of an answer."""
        if session.settings.cost_control and not confirm:
            estimate = session.estimate([request])
            if (estimate.max_usd or 0) > 0:
                return {"needs_confirmation": True, "estimate": {
                    "min_usd": estimate.min_usd, "max_usd": estimate.max_usd,
                    "cap_usd": session.settings.spend_cap_usd}}  # fmt: skip
        return None

    def bug(body: BugIn) -> str:
        if not body.description.strip() or len(body.description) > 4000:
            raise HTTPException(400, "describe the bug (up to 4000 characters)")
        return bug_text(body.description, body.area, body.fix)

    @app.get("/api/views/{name}/search")
    def search(name: str, q: str) -> list[dict[str, Any]]:
        return search_facts(facts_of(load(name)), q)

    @app.post("/api/views/{name}/impact/candidates")
    def candidates(name: str, body: BugIn) -> dict[str, Any]:
        text, view = bug(body), load(name)
        facts, session = facts_of(view), runner.session()
        request, _ = candidates_request(facts, view, text)
        if (wait := ask_first(session, request, body.confirm)) is not None:
            return wait
        cache = SummaryCache(runner.db)
        try:
            return pick_candidates(facts, view, text, session, cache)
        except _LLM_ERRORS as e:
            raise HTTPException(503, str(e)) from None
        finally:
            cache.close()

    @app.post("/api/views/{name}/impact/brief")
    def brief(name: str, body: BugIn) -> dict[str, Any]:
        text, view = bug(body), load(name)
        if not body.locations:
            raise HTTPException(400, "confirm at least one location first")
        facts, session = facts_of(view), runner.session()
        known = {n.id for n in facts.nodes}
        if unknown := [loc for loc in body.locations if loc not in known]:
            raise HTTPException(400, f"unknown location(s): {', '.join(unknown[:3])}")
        result = analyze_impact(facts, view, body.locations)
        if (wait := ask_first(session, explain_request(result, text), body.confirm)) is not None:
            return wait
        cache = SummaryCache(runner.db)
        try:
            result["explanation"] = explain(result, view, text, session, cache)
        except _LLM_ERRORS as e:  # the brief stands without the explanation
            result["explanation"] = {"summary": [], "what_to_test": [], "removed": [],
                                     "error": str(e)}  # fmt: skip
        finally:
            cache.close()
        result["bug"] = text
        result["markdown"] = to_markdown(result, view, text)
        return result

    if static_dir is not None and static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")
    return app
