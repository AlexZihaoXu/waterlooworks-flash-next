# Local extraction benchmark and overnight runner

Node 22+; LM Studio at `127.0.0.1:1234`. No credentials, external model providers, collection or model loading.

```sh
npm run benchmark -- --dry-run
npm run benchmark
npm run benchmark -- --mode salary
npm run benchmark -- --mode keywords
npm run benchmark -- --limit 1
npm run benchmark -- --dataset /path/to/private/cases.json
npm run benchmark:legacy
npm run benchmark:test
```

`benchmark` now uses the optimized **v7** profile with whichever LLM is currently loaded. Multiple loaded LLMs require `--model LOADED_INSTANCE_ID`. Generation uses the OpenAI-compatible `/v1/chat/completions` endpoint with JSON-schema constrained output. Reasoning is disabled when advertised and verified through actual token usage; other models use supported low reasoning or an explicitly unknown server default. The frozen earlier combined compensation/skills experiment remains `benchmark:legacy`.

The default private dataset is `.experiment/benchmark/cases.json`: the six original saved cases. It is **not committed**. A fresh checkout needs real, privately annotated data; no fabricated postings are seeded. Default concurrency is **4**, capped by reported server slots. The output budget is 4,096 tokens, request deadline 300 seconds, and default case limit six. With all three narrative fields present, v7 makes five calls per posting: one pay-quote pass, one whole-posting keyword pass, and three focused field passes. Terms are combined and deduplicated for reference coverage, not counted repeatedly across passes.

Final answers, model settings, source/code/prompt hashes and measured results are written privately under `.experiment/benchmarks/<run>/`. Terminal tables use `console.table()`; saved reports remain Markdown. Ctrl+C drains in-flight requests and stops new work. Incomplete runs are explicitly partial and are not comparable to complete runs.

## Dataset contract

Private JSON: `version: 1`, `cases: [...]`. Each case contains:

- `id`: unique saved posting ID as a digit string.
- `fields`: exact source field-name → nonempty source-text mapping.
- `deterministicSkills`: existing parser names; used by the legacy profile only.
- `reference.salary`: tuples `[currency, minimum, maximum, period]`. Decimal amounts are strings or null. Currency and period can be null; never infer them from location or amount. Periods: hour/day/week/month/year/null. Keep distinct eligibility tiers separate. Empty means a human expects no base pay.
- `reference.keywords`: alias groups, each an array of equivalent technical term strings. Freeze annotations before model requests.

Keep datasets, outputs, screenshots and source text ignored/private. Credential-file paths, including resolved symlinks, are refused. Output directories must remain ignored and cannot redirect through symlinks.

## Accuracy caveats

Salary amount/currency/period matching, JSON parsing and technical-name coverage are **different metrics**. Alias coverage is a limited lexical measure, not exhaustive semantic recall. Exact source quotation is not proof of correct technical classification or eligibility. The standalone extractor does **not** claim to run the older Python production validator.

The optimized salary prompt selects exact pay clauses; deterministic code parses their actual numbers, currencies and periods, rather than accepting model-rewritten values. Unknowns stay unknown. Non-base benefits and parenthesized foreign-currency alternatives are not promoted to extra primary rates. Ambiguous/unsupported quotes are withheld. Model-supplied source strings must match literally. Technical terms use Unicode-aware source boundaries, preserve punctuation and undergo source-checked slash-name expansion. Invalid terms are discarded and counted. This broader index does not assert that an applicant must know every mentioned technology.

## Optimization evidence

Seven measured rounds were run with Gemma `unsloth/gemma-4-26b-a4b-it`, Q2_K_XL, context 65,536, four server slots, reasoning off:

| Round | Change                                                                 | Salary matches | Keyword anchors |
| ----- | ---------------------------------------------------------------------- | -------------- | --------------- |
| 1     | Original combined/schema-heavy baseline                                | 1/6            | 59/87           |
| 2     | Constrained JSON, compact exact source spans, deterministic pay parser | 6/6            | 51/87           |
| 3     | Atomic technical concepts and broader domain/process coverage          | 6/6            | 68/87           |
| 4     | Source-order prompt experiment (regressed; discarded)                  | 6/6            | 53/87           |
| 5     | Focused field passes and literal slash-name expansion                  | 6/6            | 72/87           |
| 6     | Combine strongest whole-posting and focused passes                     | 6/6            | 79/87           |
| 7     | Fix parenthesized currency equivalents and open-plus rate parsing      | See below      | See below       |

The initial six held-out postings produced **5/6 salary matches, 94/105 keyword anchors and 30/30 parseable responses**. Seven non-exact spans were rejected. A salary equivalent exposed a parser bug. After fixing it under primitive regression tests, the **same-case, non-blind retest** produced **6/6 salary matches and 95/105 keyword anchors**, again 30/30 parseable responses with seven rejected spans. Do not present this retest as a new blind result or a general accuracy guarantee. Detailed private artifacts remain in `.experiment/gemma-optimization/`.

## Checkpointed overnight extraction

```sh
npm run extract -- start --all --concurrency 4
npm run extract -- status
npm run extract -- stop RUN_ID
npm run extract -- resume RUN_ID
```

The extraction command intentionally pins the **tested Gemma model, quantization, 65,536 context and four server slots**. It refuses to load or switch models. Resume requires the original model, prompts, configuration and code hashes. A ten-hour work deadline stops new requests; `--hours` changes that deadline. `--database` permits acceptance testing on a private database copy. `--posting-id` selects specific saved postings. No automatic restart/resume is installed.

The database must first be backed up consistently and migrated by the backend to schema 9. The runner never migrates implicitly. It claims one durable run, heartbeats a lease, checkpoints each source slice atomically, rejects stale source, detects other collection/inference work, and stops on model/transport/persistence failure. Expired ownership cannot commit. No source, historical observation or existing normalized projection is overwritten. Source-current/configuration-matching checkpoints can be reused; interrupted work has bounded explicit resume attempts.

**Immutable storage remains separate; read-side authority is explicitly integrated:**

- `source_extraction_runs` / `source_extraction_items`: configuration, ownership, progress and safe error counts.
- `source_extractions`: immutable source revision/hash and producer identity per completed source slice.
- `source_extraction_terms`: indexed normalized names and exact Unicode-code-point source spans.
- `source_extraction_pay`: exact-decimal **pay candidates** plus original clause/amount spans. Eligibility, hours and payout text remain available through source evidence rather than being invented.
- `source_extractions_current`: source-current rows; readers must also select the intended configuration/run.

No raw descriptions, prompts, responses or reasoning are copied into these analysis tables. Keywords can have partial, explicitly counted acceptance when individual invented spans are removed; salary responses are atomic. Source grounding is not a full semantic audit. The app now deliberately integrates selected accepted pay and keywords through a read-only evidence reader; it does not rewrite these tables or legacy projections. Compatible grounded legacy pay metadata and validated skills survive. See [read-side authority and limitations](../docs/local-api.md#saved-enrichment-authority). Reading status or opening the website never starts collection or extraction.
