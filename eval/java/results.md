# Java evaluation: 26 real bug fixes

Run 2026-11-26 with the local model `ollama:qwen3:14b`, using `rib eval eval/java/dataset.json`. The raw numbers are in [results.json](results.json).

## The test

1. Fetch and analyse the commit just before the fix.
2. Give the tool **only the issue text**: no suspected area and no likely fix.
3. The model picks the code locations from the shortlist with no human help. It never picked nothing, so the "top 3" fallback was never used.
4. Build the brief.
5. Compare it with what the real fix changed:
   - the methods whose lines the fix touched, or the class when the change was outside any method;
   - the test classes the fix touched.

The dev / held-out split comes from a hash of the case id. Nothing was tuned on either group.

## Results

| | All (26) | Dev (16) | Held-out (10) | Defects4J (14) | Spring apps (12) |
|---|---|---|---|---|---|
| **Hit**: a changed method is in the high-impact set | **18/26** | 13/16 | 5/10 | 11/14 | 7/12 |
| Class hit: a changed class is in the high-impact set | 21/26 | 14/16 | 7/10 | 13/14 | 8/12 |
| The model's picks include a changed method | 15/26 | 11/16 | 4/10 | 10/14 | 5/12 |
| Mean share of main-code methods marked high | 2.6% | 2.3% | 3.1% | 2.8% | 2.4% |
| Mean test recall (cases whose fix touched tests) | 0.62 (15) | 0.79 (8) | 0.43 (7) | 0.83 (10) | 0.20 (5) |

How to read this:
- A hit means the brief warned about code the real fix went on to change, while marking only about 1 in 40 methods as high impact (median 1.9%).
- Hits without a correct pick (spring-petclinic #207 and #108, Cli-1) happened because the traversal reached the changed method from a neighbouring location.
- Test recall follows location. On the 8 hits whose fix touched tests, recall is 1.0 in 7 cases and 0.33 in one (Codec-2). Every 0.0 is a case where the location was missed.
- **Held-out is worse than dev: 5/10 against 13/16.** With 10 cases the gap could be chance, but it isn't a reason to trust the dev number.

## Misses, analysed by hand

| Case | What the issue says | What the model picked | Why it missed |
|---|---|---|---|
| start.spring.io #2263 | Only a title: "Unable to create Spring Boot 4.2.x projects" | Boot-version constants and the Java-version customizer | The fix is in `JavaVersionMapping`, which the title doesn't point to. **Vague issue text** |
| d4j-Codec-1 | "language codecs are platform-dependent", plus a link | the `net` codecs' `charset` fields | The fix is the `toUpperCase()` calls in `language.*`. The words "case" and "locale" never appear. **Vague issue text** |
| start.spring.io #2271 | LDAP test starter | `AddTestStartersBuildCustomizer` | The fix is in `LdapUnboundIdBuildCustomizer`. **Plausible neighbour picked** |
| start.spring.io #1458 | Postgres / pgvector compose service | Docker service resolver methods | The fix is in the two `*ProjectGenerationConfiguration` customizers that call the resolver. These are callers, and callers of a pick are only reached if the pick is a method they call. **Plausible neighbour picked** |
| start.spring.io #2120 | Main class naming | Testcontainers name constants | The fix is in a `ProjectDescriptionCustomizerConfiguration` bean. **Wrong area** |
| start.spring.io #1217 | Testcontainers service connections | Sibling methods in the right three classes (class hit) | The fix is in `contributeCode` of the same classes. **Right class, wrong method** |
| d4j-Jsoup-2 | Parser behaviour on a tag | `Parser#parse`, `Tag` methods (class hit) | The fix is in `Parser#parseStartTag`. **Right class, wrong method** |
| d4j-Jsoup-1 | "leading text moved to the back" after `Jsoup.parse` | `Jsoup#parse`, `Element#text` | The fix is in `Document#normalise`, which `parse` *calls*. Impact goes up to callers, never down to callees, so a bug that shows at the entry point but lives below it isn't reached. **Gap in the method** |

Patterns:
1. Three misses stay in the right class or on the right call path.
2. Two misses have issue text that names nothing in the code.
3. Only one miss is a clear model error (#2120).
4. Spring-app test recall is low because every Spring miss is also a test miss. On the one Spring hit with changed tests (#1686), recall was 1.0.

## Not measured

- **The size signal** (looks small / looks big / can't tell). There is no ground truth for "how big was the impact". Its thresholds stay untested.
- **Summaries were not used.** Location picking with class summaries might do better, but making them takes the local model hours on the larger repos.
- **A suspected area from a person.** In the real tool someone confirms or corrects the locations, so these numbers are a lower bound for that step.
