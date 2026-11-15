"""Analyses run one at a time on a background thread (a local model can't do more),
reporting their stage and progress. With a paid model and cost control on, a job
stops after the estimate and waits until the user confirms or cancels."""

import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from release_impact_brief.facts import FactStore
from release_impact_brief.fetch import FetchError, FetchResult, fetch_repo, parse_github_url
from release_impact_brief.llm import (
    CostUnknown,
    LlmAuthError,
    LlmError,
    LlmSession,
    LlmUnavailable,
    Provider,
    Settings,
    SpendingCapExceeded,
    load_settings,
    make_provider,
)
from release_impact_brief.pipeline import analyze
from release_impact_brief.summaries import Summarizer, SummaryCache
from release_impact_brief.views import save_view

log = logging.getLogger(__name__)
_CONFIRM_TIMEOUT = 3600.0  # seconds a job waits for a yes before giving up
_EXPECTED = (FetchError, LlmAuthError, LlmUnavailable, LlmError, SpendingCapExceeded, CostUnknown)

Fetch = Callable[[str, str | None], AbstractContextManager[FetchResult]]


@dataclass
class Job:
    id: str
    url: str
    ref: str | None
    # queued, fetching, extracting, awaiting_confirmation, summarizing, viewing,
    # then done, failed or cancelled
    status: str = "queued"
    total: int = 0
    completed: int = 0
    view: str | None = None  # saved view name once done
    estimate: dict[str, float] | None = None
    error: str | None = None
    session: LlmSession | None = field(default=None, repr=False)
    decided: threading.Event = field(default_factory=threading.Event, repr=False)
    confirmed: bool = False

    def to_dict(self) -> dict[str, Any]:
        completed = self.completed
        if self.status == "summarizing" and self.session is not None:
            completed = min(self.session.completed, self.total)
        return {"id": self.id, "url": self.url, "ref": self.ref, "status": self.status,
                "total": self.total, "completed": completed, "view": self.view,
                "estimate": self.estimate, "error": self.error}  # fmt: skip


class JobRunner:
    def __init__(self, db: Path, fetch: Fetch = fetch_repo,
                 settings: Callable[[], Settings] = load_settings,
                 provider: Callable[[Settings], Provider] = make_provider) -> None:  # fmt: skip
        self.db = db
        self.views_dir = db.parent / "views"
        self._fetch, self._settings, self._provider = fetch, settings, provider
        self._jobs: dict[str, Job] = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analysis")

    def start(self, url: str, ref: str | None) -> Job:
        parse_github_url(url)  # raises FetchError for anything but https://github.com/<o>/<r>
        job = Job(uuid.uuid4().hex[:12], url, ref or None)
        self._jobs[job.id] = job
        self._pool.submit(self._run, job)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def decide(self, job_id: str, confirmed: bool) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.status != "awaiting_confirmation":
            return False
        job.confirmed = confirmed
        job.decided.set()
        return True

    def _run(self, job: Job) -> None:
        cache: SummaryCache | None = None
        try:
            settings = self._settings()
            session = LlmSession(self._provider(settings), settings)
            job.session = session
            job.status = "fetching"
            with self._fetch(job.url, job.ref) as fetched:
                job.status = "extracting"
                facts = analyze(fetched.path, fetched.skipped_large)
                self.db.parent.mkdir(parents=True, exist_ok=True)
                store = FactStore(self.db)
                try:
                    store.replace_facts(store.snapshot(fetched.repo.url, fetched.commit_sha),
                                        facts.nodes, facts.edges)  # fmt: skip
                finally:
                    store.close()
                cache = SummaryCache(self.db)
                summarizer = Summarizer(facts, fetched.path, fetched.repo.url,
                                        fetched.commit_sha, session, cache)  # fmt: skip
            plan = summarizer.plan()
            job.total = plan.class_requests + plan.later_requests + 1
            if settings.cost_control and (plan.max_usd or 0) > 0:
                job.estimate = {"min_usd": plan.min_usd or 0.0, "max_usd": plan.max_usd or 0.0,
                                "cap_usd": settings.spend_cap_usd}  # fmt: skip
                job.status = "awaiting_confirmation"
                if not job.decided.wait(_CONFIRM_TIMEOUT) or not job.confirmed:
                    job.status = "cancelled"
                    return
            job.status = "summarizing"
            result = summarizer.run()
            job.status = "viewing"
            path, _ = save_view(facts, result, summarizer.repo, summarizer.sha, session, cache,
                                self.views_dir)  # fmt: skip
            job.view, job.completed, job.status = path.stem, job.total, "done"
        except _EXPECTED as error:
            job.error, job.status = str(error), "failed"
        except Exception:
            log.exception("analysis %s failed", job.id)
            job.error, job.status = "unexpected error (see the server log)", "failed"
        finally:
            if cache is not None:
                cache.close()
