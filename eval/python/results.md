# Python evaluation: 21 BugsInPy fixes

Run 2026-11-26 with the local model `ollama:qwen3:14b`, using `rib eval eval/python/dataset.json`. The raw numbers are in [results.json](results.json). The method is the same as for [Java](../java/results.md): only the issue text, the model picks locations alone, the dev / held-out split comes from a hash, and the ground truth comes from the fix's diff.

## Cases

[BugsInPy](https://github.com/soarsmu/BugsInPy) (FSE 2020) lists real bugs with their buggy and fixed commits. The cases were filtered in four steps:

1. **Fix names an issue.** A bug was kept only if its fix commit names an issue, as "Fix #N" or as the first of two numbers. This gave 22 hand-picked bugs from 11 projects, at most 3 per project.
2. **Issue, not a pull request.** A number that turned out to be a pull request was dropped; that was spacy-3. **21 were kept.**
3. **Projects left out:** pandas, ansible and matplotlib are too big for the local model; youtube-dl has malformed bug files.
4. **Issue text as written.** Some issues quote the fix itself; PySnooper #124 quotes the one-line patch. They were kept, as for Java.

## Results

| | All (21) | Dev (12) | Held-out (9) | Java, for comparison (26) |
|---|---|---|---|---|
| **Hit**: a changed method is in the high-impact set | **7/21** | 4/12 | 3/9 | 18/26 |
| Class hit | 8/21 | 5/12 | 3/9 | 21/26 |
| **The shortlist held the changed code** | **7/21** | 4/12 | 3/9 | 18/26 |
| The model's picks include a changed method | 7/21 | 4/12 | 3/9 | 15/26 |
| Mean share of main-code methods marked high | 2.4% | 3.3% | 1.2% | 2.6% |
| Mean test recall (cases whose fix touched tests) | 0.50 (18) | 0.44 (9) | 0.56 (9) | 0.62 (15) |

How to read this:
- **Every miss is a search miss.** The model chooses only from a 25-item shortlist built by word matching. In all 14 misses the changed code wasn't on that shortlist. In the 7 cases where it was, the model picked it every time; for Java the rate was 15 of 18.
- So the problem is not the model or the traversal. It is the word-match search, which works much worse on these Python issues than on the Java ones.
- **Not yet a fair comparison.** The Java shortlist number was measured afterwards with the same code, offline and without the model.

## Misses, analysed by hand

"Rank" is where the changed code came in the search; the shortlist keeps the top 25.

| Case | What the issue says | Rank | Why it missed |
|---|---|---|---|
| thefuck-6 | names `git_branch_exists` | 30 | **Code named in the issue but not boosted**: generic words like "command" score the same |
| luigi-3 | names `TupleParameter`, plus a 4000-character traceback | 288 | **Code named but not boosted**; the traceback adds dozens of terms |
| luigi-5 | names `util.inherits` and `requires` | 44 | **Code named but not boosted** |
| sanic-2 | names `AsyncioServer` | 279 | **Code named but not boosted**; the fix is in the class (`__getattr__`) |
| scrapy-37 | names `scrapy.Request` | 219 | **Code named but not boosted** |
| scrapy-5 | names `response.follow` | 34 | **Code named but not boosted**; the model picked the subclass override `TextResponse.follow` |
| fastapi-9 | Body media types vs. requestBody | 35 | Just outside the shortlist; the model picked a sibling in the same module (class hit) |
| black-19 | blank lines after a comment between decorators | 28 | Just outside the shortlist |
| black-13 | can't parse an async generator | 59 | The fix is in the tokenizer, which `lib2to3_parse` (picked) *calls*. Impact goes up to callers, never down to callees. **Same gap as Java's Jsoup-1** |
| keras-33 | tokenization with a multi-character split | 59 | `Tokenizer.fit_on_texts` (picked) *calls* the fixed `text_to_word_sequence`. **Same callee gap** |
| black-10 | tab-indented comments after a dedent | 248 | The bug is in the vendored parser (`blib2to3`), far from the formatting code the symptom points to |
| fastapi-14 | `additionalProperties` with dicts | 235 | The fix is in the OpenAPI model classes; the issue describes only the generated schema |
| thefuck-1 | crash output for `pip un+install` (4000 characters) | 155 | Noisy text; the rule's module (`pip_unknown_command`) isn't named |
| thefuck-2 | only the title: "No fucks given" | no match | **Vague issue text** |

Patterns:
1. **6 misses name the code in the issue**, and 2 more rank just outside the shortlist (28 and 35). Java's misses had no such pattern; their issues rarely named identifiers.
2. 2 misses are the known callee gap.
3. 4 are vague, or the symptom is far from the cause.

## Found and fixed while running this

All three bugs below were in the Python port (part 1). Each has a test that fails without the fix and passes with it.

- **Shortlist filled with tests.** The search skipped tests with a Java-only path check, so Python test functions filled the shortlist; in thefuck-6 the model picked only tests. Fixed, and the whole set was re-run; the hit count was also 7/21 before the fix.
- **Two infinite-recursion bugs in call resolution.** Both were found on real repos while the dataset was being built.

## Not measured

- **The same things as for Java:** the size signal, summaries, and a person confirming the locations.
- **Whether boosting identifiers named in the issue would help.** The analysis above suggests it would. But the idea came from looking at these 21 cases, so testing it on them would not count; it needs bugs nobody has looked at. See STATUS.
