# Weighted keyword coverage

`frontend/src/lib/keywordCoverage.ts` is a pure TypeScript utility shared by the Node benchmark and the Similar jobs worker. The utility itself does not call a model, fetch postings, or persist analyses.

## Formula

For each target keyword, `weight = Math.log1p(sourceOccurrenceCount)`. Coverage is `100 * matchedWeight / totalWeight`. A keyword earns its weight once if present in the comparison text, regardless of repeated mentions. An empty usable keyword set returns `score: null`; a usable target with no matches returns zero.

```ts
const profile = prepareKeywordProfile(sourceFields, extractedNames);
const result = scoreKeywordCoverage(comparisonText, profile);
// result.score, result.matched, result.missing, result.totalWeight
```

Both matched and missing entries include the canonical keyword, source occurrence count, and weight. Profiles retain compiled matchers, not copies of source text. Reuse a profile across queries; if caching, key it by source content hash, extracted keyword set, algorithm version and alias configuration. Rebuild profiles on version changes. Profiles are runtime objects, not JSON-serializable matcher caches.

Similar jobs prepares a shared target index and uses the batch API:

```ts
const search = prepareKeywordSearch(targetProfiles);
const results = scoreKeywordCoverageBatch(comparisonText, search);
// Results retain target order and the same full matched/missing explanations.
```

Version2 uses fast ASCII boundary checks with exact Unicode-expression fallback, safe literal prechecks, and one scan per distinct keyword matcher across targets. Alias configuration is part of matcher identity. Unicode case folding, technical punctuation, source overlap selection, weights and scoring semantics are unchanged. The implementation does not cache answers by query text or skip explanation construction to inflate benchmark results.

## Matching policy

- Count original source text, not extraction evidence rows or overlapping extraction passes.
- Search each source field independently; never manufacture phrase occurrences across field boundaries. Field labels are not counted.
- Match case-insensitively with flexible whitespace inside phrases. Preserve technical punctuation: Java does not match JavaScript; C does not match C++ or C#; Node does not match Node.js.
- Merge a small explicit alias list before counting (for example JavaScript/JS). Pass an empty alias list for literal-only matching. Custom alias collisions fail rather than silently changing meaning.
- Source occurrences use deterministic leftmost-longest overlap selection. A nested phrase cannot multiply the count of the same source span; another standalone occurrence can count independently. Names with no surviving source occurrence are exposed as `excludedKeywords` and receive no denominator weight.
- No fuzzy matching, stemming, semantic embeddings, negation handling or required/optional weighting is claimed.

The score is directional: comparing A's text against B's keyword profile need not equal comparing B's text against A's profile. It measures weighted coverage, not a probability of suitability or guaranteed semantic similarity. Short/sparse keyword profiles may achieve high coverage easily. Keep matched/missing evidence and index completeness visible when using it for Similar jobs; source-current checks and stable posting-ID tie breaks are also necessary. A symmetric metric would be a separate explicit product decision.

## Similar jobs integration

`GET /api/keyword-index` exposes only saved field values, deduplicated names, source hashes, partial-status flags and index metadata. It selects the latest run containing keyword work and includes only terminal, source-current postings from that run/configuration. It does not collect, infer or modify data. Pending/stale postings are excluded; empty usable profiles are unavailable, not zero-confidence predictions.

The API takes a consistent read-only snapshot and returns an ETag derived from run/configuration identity, source hashes, item states and immutable-term append generation. Conditional requests return304 without transferring the source corpus again. A content/index change invalidates the worker's profiles. Legacy `/comparisons` remains available for compatibility, but the posting-page client no longer calls it or waits on it.

`similarJobs.worker.ts` fetches/revalidates the index when a ready posting is selected or the refresh revision changes. It prepares weights off the UI thread, reuses profiles while the index revision is unchanged, scans all usable targets, excludes self/zero/unavailable results, breaks score ties by posting ID, and returns the top12 with explanations. The comparison text is the currently displayed posting's saved fields. Main-list filters do not restrict this corpus. There is no polling, model call or automatic refresh. Superseded requests are cancelled and stale responses ignored. Index failures leave the description readable and show an explicit unavailable state instead of stale rankings.

The sidebar uses existing HeroUI Card, Chip and Link compositions. Ranking is described as **keyword coverage—not eligibility**, but percentages and matched/missing breakdowns are not displayed. Scores, source counts and log weights remain available internally; partial indexes are marked. Pay and posting metadata still come from the existing workspace contracts, with unchanged source/display authority.

The first visit downloads the local source corpus and prepares profiles once; it is slower than cached navigation. The optimized scoring microbenchmark excludes this initial download and UI rendering. The corpus stays in worker memory only—no browser local-storage copy is written.

## Real-data speed benchmark

From the repository root, with Node22.22.3+ and the local schema9 database:

```sh
npm run benchmark:coverage
```

This command uses the same TypeScript implementation through Node's type stripping. It reads `data/waterlooworks.sqlite3` in a bounded read-only snapshot, chooses the latest extraction run, and samples one source plus100 distinct targets uniformly without replacement using `crypto.randomInt`. Only source-current postings with terminal keyword slices are eligible; partial/rejected/failed slices are not silently resampled. Targets with no usable keywords remain in the sample as unavailable scores.

The input string is the source posting's saved plain-text field values joined with blank lines, not reconstructed HTML. Frequency weights use each target's original saved fields and deduplicated extracted names. The benchmark checks self-coverage, then measures:

1. Database read and snapshot assembly.
2. Preparing100 weighted profiles and a shared matcher index (reported separately).
3. First batch scoring pass against all100 targets.
4. Fifty repeated scoring batches after five warm-up batches (median, p95, min/max and mean per comparison).

Every timed scoring pass computes matched/missing explanations as well as the scalar score. First-pass timings exclude Node startup/imports and final report serialization; they are not a claim about a cold operating-system cache. Warm results reuse prepared profiles and benefit from JavaScript regex/JIT warm-up. They do not include browser rendering, network latency or future API overhead. Run profile preparation/scoring away from the UI thread if integrating a large corpus interactively.

Original private snapshots, selected IDs, eligible population, source/configuration hashes, code hash, timings and scored results are written only under ignored `.experiment/keyword-coverage-*`. The command accepts no alternate input/output paths and performs no network requests, inference or refresh. A new invocation takes a new random sample; the saved IDs/snapshots provide the original run's reproducibility evidence.
