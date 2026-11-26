"""Phase 6 done check: hand-written petclinic questions answered by the real model.

Run with `pytest -m "llm and network" tests/test_chat_eval.py -s` (needs the model
in RIB_MODEL - by default the free local Ollama one - and a few minutes).
"""

import json
from pathlib import Path

import pytest

from release_impact_brief.chat import ask
from release_impact_brief.clones import ensure_clone, read_lines
from release_impact_brief.fetch import fetch_repo
from release_impact_brief.llm import LlmSession, load_settings, make_provider
from release_impact_brief.pipeline import analyze
from release_impact_brief.summaries import Summarizer, SummaryCache
from release_impact_brief.views import save_view

KEY = Path(__file__).parent / "fixtures" / "spring-petclinic" / "chat.json"


@pytest.mark.llm
@pytest.mark.network
def test_every_statement_is_cited_and_answers_find_the_right_code(tmp_path: Path) -> None:
    key = json.loads(KEY.read_text())
    settings = load_settings()
    session = LlmSession(make_provider(settings), settings)
    cache = SummaryCache(Path(".rib/facts.sqlite"))  # reuse cached summaries if present
    with fetch_repo(key["repo"], key["commit_sha"]) as fetched:
        facts = analyze(fetched.path)
        result = Summarizer(facts, fetched.path, key["repo"], key["commit_sha"], session,
                            cache).run()  # fmt: skip
        _, view = save_view(facts, result, key["repo"], key["commit_sha"], session, cache,
                            tmp_path)  # fmt: skip
    checkout = ensure_clone(key["repo"], key["commit_sha"], Path(".rib/clones"))
    hits, shown, removed = 0, 0, 0
    for item in key["questions"]:
        answer = ask(facts, view, checkout, item["q"], item["context"], session, cache)
        cited = [c for s in answer["segments"] for c in s["citations"]]
        for s in answer["segments"]:
            assert s["citations"], s  # nothing uncited is ever shown
            for c in s["citations"]:
                assert read_lines(checkout, c["file"], c["start"], c["end"]), c  # real lines
        hit = any(c["file"] == w["file"] and c["start"] <= w["end"] and w["start"] <= c["end"]
                  for c in cited for w in item["must_cite_any"])  # fmt: skip
        hits += hit
        shown += len(answer["segments"])
        removed += len(answer["removed"])
        print(f"\n{'HIT ' if hit else 'MISS'} {item['q']}")
        for s in answer["segments"]:
            print(
                "   -",
                s["text"],
                [f"{c['file'].rsplit('/', 1)[-1]}:{c['start']}" for c in s["citations"]],
            )
        for r in answer["removed"]:
            print("   x", r["text"], f"({r['reason']})")
    print(f"\nhits {hits}/{len(key['questions'])}, statements shown {shown}, removed {removed}")
    cache.close()
    assert hits >= 7, f"only {hits} of {len(key['questions'])} answers cited the expected code"
