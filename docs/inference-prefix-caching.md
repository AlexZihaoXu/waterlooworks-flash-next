# Shared job-detail KV prefix design

Status: integrated as the **experimental opt-in** `--shared-job-prefix` extraction profile. The default remains the validated v7 request/scheduling; standalone country requests stay narrow. No production run, model call or salary/keyword re-extraction was performed during integration. Live accuracy and multi-worker cache effectiveness remain acceptance gates.

## Request layout

`scripts/shared-job-prompt.mjs` builds two chat messages:

1. One fixed system instruction shared by every task.
2. One user message: complete canonical saved job-detail JSON, then a task-specific suffix.

The source prefix must not contain task/mode/part names, response schemas, run IDs, timestamps or task-specific instructions. Field keys are sorted deterministically, values are preserved without truncation, and model role delimiters are escaped. Keeping data and task in one user message also avoids incompatible consecutive-user turns in chat templates.

The opt-in salary and keyword tasks use exactly the same complete detail fields and serialization. `sharedCountryMessages(details, allowedFields, instructions)` offers a country composition adapter using that identical prefix, without changing the standalone country runner. Pass the restricted fields from `countryInput`, not all detail fields, and keep its unknown/oversize handling, output schema and evidence validator. This adapter does not declare a persisted country profile: do not save its outputs under the existing narrow-country configuration. Use it for private validation until a separately versioned country profile is approved. A task suffix maps its authorized evidence aliases (such as `f0` or `content:Job - Country:`) to the original detail field names, without copying their text again. Listing-only information belongs in the task suffix: existing salary/keyword source identity depends on detail content, not listing revisions. Structured-output schemas remain request parameters, and existing task-specific exact-span validators are unchanged. `sourceFields` retains its original field order and alias numbering; sorting the shared prefix never renumbers `f0`/`f1` or changes the selected slice. Mappings are checked against the original field name and exact text before construction. Full request budgeting counts UTF-8 bytes of the entire JSON body (including schema and suffix), reserves the completion limit plus512 tokens, and rejects `context-budget` rather than truncating. This is the existing conservative byte-based bound, not a model-tokenizer measurement; a future country adapter caller must apply the same full-body bound.

This makes the complete job context a reusable token prefix. It does not implement, persist or guarantee a provider KV cache.

## Scheduling

The opt-in runner now uses a posting-affine task queue; default v7 retains its slice-affine queue:

- One worker owns a posting and executes its required slices sequentially.
- The first useful task performs the initial prefill; do not spend an extra request just warming the cache.
- Continue with that posting's remaining task suffixes before moving to another posting.
- Keep durable per-slice checkpoints and source/configuration identities. Skip terminal matching results; restart or cache eviction must not imply successful inference.
- Run at most four distinct posting workers. Never concurrently cold-prefill several slices of the same posting.
- Benchmark one, two and four posting workers before selecting concurrency. Standard OpenAI-compatible requests do not give us a documented slot-affinity guarantee; four application workers alone cannot guarantee four retained job contexts.

`PostingQueue` holds in-memory worker-to-posting reservations beneath the exclusive database run lease. `claimItem` still checks lease, stop, source lock and content freshness inside `BEGIN IMMEDIATE`; each slice is still individually claimed and checkpointed. A reservation lasts through the checkpoint/next-claim gap. Available candidates exclude running slices from that posting and other workers' reservations, and are scoped to the run. Workers retain their posting until its pending slices are exhausted, then take the next unreserved posting. A worker with only temporarily reserved work available retires; the reserved postings' active owners continue draining them. The runner waits for **all** workers and then checks pending/running counts, so a blocked worker does not mark the run completed. Stop, deadline or fatal failure leaves durable remaining work rather than dropping it.

Reservations are not persistent provider-slot handles. After explicit expired-run resume a new queue is built from durable pending checkpoints; accepted/partial/stale/rejected checkpoints remain terminal. The existing extraction attempts policy is unchanged: lost running/failed items retry only while attempts<2. Country's separate no-reissue policy is unchanged. No schema changes or new posting locks are added.

## Local experiment

A private saved-posting probe used the already-loaded Gemma model, reasoning off, unchanged model configuration, `store:false`, and five country-only requests. No salary/keyword extraction or production writes occurred. All five country responses passed exact-evidence validation.

| Layout/order                                         | First-token latency |
| ---------------------------------------------------- | ------------------: |
| Shared prefix, initial request                       |             3001 ms |
| Shared prefix, adjacent changed task suffix          |              562 ms |
| Task first, variant A                                |             1261 ms |
| Task first, variant B                                |             1276 ms |
| Shared prefix, after interleaved task-first requests |             1268 ms |

These single-posting timings support adjacency as an optimization and show that reuse cannot be assumed after interleaving. They are **not** cache-hit counts, isolated prefill measurements, accuracy estimates or full-corpus throughput benchmarks. The endpoint reported total prompt tokens but no cached-token count. Private evidence: `.experiment/country-pilot/cache-probe.json`.

## Rollout gates

1. Implemented: opt-in profile `v8-shared-job-prefix`, prefix version `shared-job-details-v1`, scheduling version `posting-affine-v1`. Configuration hashes include these values, existing prompt/code hashes and the shared builder file hash. The source-span validator version remains v7 because the output/evidence protocol is unchanged. Switching profiles or editing source refuses incompatible cache reuse/resume. Existing stored results remain readable; even default-v7 historical resumes can refuse after code changes, as before. No hash bypass or silent fallback is introduced.
2. Implemented: posting ownership under the existing run lease with per-slice checkpoints, stop/deadline handling and source freshness. Primitive/empty-SQLite tests cover reservations, four interleaved workers, blocked work, run scope, terminal skipping, stop/lease refusal, rollback and original bounded expired-run recovery.
3. Check the actual rendered model prompt if schema-dependent framing could alter the common prefix. Measure bounded one/two/four-worker trials; record cached-token metrics only if the backend actually exposes them.
4. Re-run real evidence/accuracy acceptance for each task before enabling its new prompt layout. More shared context can introduce distraction; exact quote validation alone does not establish correctness.
5. Do not change model loading/configuration, add undocumented `slot_id`/`cache_prompt` parameters, or enable server-side stored conversations to manufacture a cache claim.

## Explicit commands and parent-owned verification

No flag means the original v7 layout/scheduler. Opt in only on a consistent **private schema9/10 backup**, never the production database for experimental acceptance:

```sh
npm run extract -- start --database .experiment/kv-validation/trial.sqlite3 --all --limit 4 --concurrency 1 --hours 0.1 --shared-job-prefix
npm run extract -- status RUN_ID --database .experiment/kv-validation/trial.sqlite3
npm run extract -- stop RUN_ID --database .experiment/kv-validation/trial.sqlite3
npm run extract -- resume RUN_ID --database .experiment/kv-validation/trial.sqlite3 --concurrency 1 --hours 0.1 --shared-job-prefix
```

Resume must repeat the original flag and concurrency/model configuration. `--hours` is a per-invocation deadline, not part of configuration identity. Start creates salary AND keyword slices; it is **not** a country-only probe and requires separate owner authorization. The CLI switch does not enable country inference or imply that different CLI runs share KV state.

### Integrated country-only verification

Parent reran all41 Node tests successfully after independent review found no must-fix defects. The integrated country adapter then passed24/24 real country-only requests across four saved postings (CA/US/CN/DE), with two sequential suffix variants per posting at concurrency1/4/2. Production and salary/keyword extraction were untouched.

Median first-token latency (first variant / adjacent second variant): concurrency1 **926/284ms**, concurrency2 **1646/694ms**, concurrency4 **1091/2134ms**. Four-worker adjacency did not show the same latency benefit. These bounded, non-randomized timings do not prove cache hits or rank overall throughput; no cached-token metrics were exposed. Prefer explicit concurrency1 or2 for further cache-focused acceptance, rather than assuming four workers preserve KV affinity. Real salary/keyword quality and provider framing remain unverified; the profile stays experimental and opt-in. Private evidence: `.experiment/country-pilot/integrated-probe.json`.

Remaining parent verification recipe:

1. First review code and run `npm run benchmark:test`. Before live work, explicitly confirm collection, legacy inference, country and source extraction are idle and that no other client is using the model. With zero model calls, use empty SQLite/queue metadata to inspect leases and draining. All tracked tests use primitive fields/IDs, not invented postings/provider responses.
2. For a country-only cache probe, privately read one real listing/detail pair, call `countryInput`, then the country adapter with its allowed fields and existing country instructions. Use `countryOutputSchema(input.fields)`, `store:false`, the existing loaded model, reasoning off and bounded completions; validate with `validateCountry`. Preserve source exclusions and preflight/oversize guards. Keep source/provider text out of public artifacts.
3. Only after separately authorized salary/keyword acceptance, create **fresh identical private backups** for concurrency1,2,4. Run the same small, reviewed real saved-ID set with the opt-in flag (repeat `--posting-id` instead of `--all --limit` to fix selection). Fresh copies avoid matching result-cache hits; they do not clear provider KV, so counterbalance trial order and record warm/cold ambiguity. Inspect per-slice status, exact spans and salary/keyword accuracy before accepting the profile. No full-workspace rerun is needed for this gate.
4. Observe that each posting has at most one running slice and the same worker continues its slices. Compare first-token latency and throughput at equivalent output limits. Current CLI stores overall seconds and token totals, not TTFT or cached-token counts; use a bounded private streaming harness for TTFT and report cached counts only if actually exposed. Different task schemas may affect provider-rendered framing. Do not call the profile cache-effective from scheduling tests alone.
5. During a private trial, stop and resume with the same configuration; terminal checkpoints must not be reissued. Test wrong-profile resume refusal, stopped/deadline remaining counts, explicit expired recovery and source changes hiding stale results. All production migration/service work remains parent-owned.

The currently approved country-only scan already makes one request per posting. Expanding it to a full-detail shared prefix would add input without removing a duplicate prefill. Its earlier salary/keyword run used a different layout and was completed long ago; its KV state cannot be assumed available. Keep the narrow country request until a genuinely shared multi-task run is ready.

References: [LM Studio Chat Completions](https://lmstudio.ai/docs/developer/openai-compat/chat-completions), [Chat API statistics](https://lmstudio.ai/docs/developer/rest/chat), [Stateful chats](https://lmstudio.ai/docs/developer/rest/stateful-chats). Stateful conversation history/response IDs are not a guarantee of resident KV reuse. MLX cache claims are not evidence for this Windows llama.cpp runtime.
