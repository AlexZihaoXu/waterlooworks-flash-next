# Local listings API

Schema10 adds a separate, read-only `country_inference` contract to workspace postings and posting detail. Existing country source facts/nulls and pay/keyword authority are unchanged. The explicit local country CLI, source-current selection, SVG flag UI, and deployment/pilot safeguards are documented in [inferred work country](country-inference.md). No model calls run on API GET/startup/SSE.

The API is **single-user and loopback-only**. Do not expose it publicly or run multiple Uvicorn workers. No containers are required for local verification.

## Start explicitly

From the repository root, in Git Bash:

```sh
# Read-only server: manual collection is disabled by default.
uv run --project backend --locked uvicorn waterlooworks_flash_next.api:create_app --factory --host 127.0.0.1 --port 8787 --no-access-log

# Enable explicit manual collection instead (may open Chromium and prompt for Duo).
WW_ENABLE_REFRESH=1 uv run --project backend --locked uvicorn waterlooworks_flash_next.api:create_app --factory --host 127.0.0.1 --port 8787 --no-access-log
```

Run only one of these commands. Do not enable reload while a collection is active. Chromium must already be installed through Playwright. `WW_DATABASE` optionally selects a database path; `WW_PROJECT_ROOT` selects the repository containing the user-managed runtime credentials and private browser profile. Defaults assume the repository root working directory.

- **Human documentation:** <http://127.0.0.1:8787/doc> (Swagger UI).
- **Machine schema:** <http://127.0.0.1:8787/doc/openapi.json>.
- **Tracked schema:** [`openapi.json`](openapi.json), generated from the same FastAPI models.
- **Generated frontend types:** `frontend/src/lib/api.generated.ts`.

Swagger UI's Execute button is a real API call. Executing the refresh operation starts collection only when enabled; it obeys exactly the same lock/cooldown as the frontend. Swagger UI assets currently use FastAPI's default CDN.

## Routes

| Method | Route                            | Result                                                                                           |
| ------ | -------------------------------- | ------------------------------------------------------------------------------------------------ |
| GET    | `/api/health`                    | Service status and SQLite schema version                                                         |
| GET    | `/api/workspace`                 | Stored listings and their revision histories; never triggers external fetching                   |
| GET    | `/api/postings/{id}`             | Current saved listing and descriptive content                                                    |
| GET    | `/api/postings/{id}/analysis`    | Current-source facts, reconciled pay, keywords, per-mode extraction status and application route |
| GET    | `/api/postings/{id}/history`     | Actual append-only listing observations with timestamp provenance                                |
| GET    | `/api/postings/{id}/comparisons` | Legacy overlap cohort retained for compatibility; no longer used by Similar jobs                 |
| GET    | `/api/keyword-index`             | Source-current saved fields/keywords for worker scoring; supports ETag/304                       |
| GET    | `/api/refresh`                   | Current progress and bounded sanitized logs                                                      |
| POST   | `/api/refresh`                   | Explicit `{}` JSON body; 202 with run ID if accepted                                             |
| GET    | `/api/refresh/events`            | SSE events named `refresh`; data conforms to `RefreshSnapshot`                                   |

`?once=true` returns one SSE snapshot and closes, for clients testing the stream format. Normal streams send current state on connection and changes thereafter, with keep-alive comments. Reconnection never starts collection. Event payloads are documented in OpenAPI using the `RefreshSnapshot` schema and an `x-event-payload` reference.

Workspace and keyword-index responses have bounded, single-entry in-memory caches. Each cache uses a persistent read-only SQLite connection's `PRAGMA data_version` plus database file identity, not a time-based freshness guess. Any commit invalidates reuse; a commit during assembly prevents publishing that result to the cache. Concurrent loads are coalesced, and observers close at shutdown. Large responses use gzip; `text/event-stream` is excluded so progress remains unbuffered. Browser cache directives and keyword ETag behavior are unchanged.

The workspace endpoint is a read-only local snapshot, not a live WaterlooWorks request. It currently returns all stored listings/history for client-side filtering and pagination. Larger histories will require server-side paging later. Country remains null in this listing-only response; stored content is not joined or exposed as a detail view. `details_available=false` describes that UI/API scope, not the absence of content collection.

The keyword-index endpoint is read-only. It selects one latest keyword run/configuration, excludes pending/stale sources and marks partial outputs. Its ETag changes with source hashes, item states or index append generation; `If-None-Match` avoids repeating the full corpus transfer. The browser worker prepares and caches profiles, then ranks the top12 positive weighted-coverage matches outside the UI thread. It does not infer applicant eligibility or replace verified compensation. See [keyword coverage](keyword-coverage.md).

## Saved enrichment authority

`analysis_read.read_analysis` and workspace compensation use the same saved-source enrichment reader. Listing and Similar jobs cards consume workspace `compensation_sort.source_rates`; detail consumes the identical reconciled rates. No GET runs collection, normalization, inference, migrations or writes. Source extraction storage and deterministic/application/history projections are unchanged.

For each mode (salary and keywords), the reader selects the latest run containing that mode, ordered by creation time and run ID—not a posting's last successful extraction. Every selected posting/mode slice must be terminal and match current saved content before any accepted children are exposed. Accepted/partial children must match item posting, content hash, mode, part, rejection count and selected configuration hash, with valid immutable source revision references. The configuration JSON must hash to that identity. Same-configuration cached extractions may originate in an older run. Exact bounded Unicode-code-point evidence spans are checked on read; keyword names must normalize from those spans. Pending, stale, rejected and failed children never become accepted output through fallback to an older source run.

`salary_extraction` and `keyword_extraction` independently report `accepted`, `partial`, `empty`, `rejected`, `failed`, `stale`, `pending` or `not_requested`, with selected run/configuration and slice counts. Empty means accepted extraction with no usable output, not unavailable work or proof that no pay/keywords exist. Partial can include mixed terminal outcomes or withheld invalid evidence. Existing `model_status` describes only the legacy model projection; the new per-mode statuses are the primary UI enrichment status. `keywords` are grounded source mentions, not required/preferred skills or eligibility assertions; validated `skills` remain separate.

New pay is integrated by amount/rate clause rather than broad quote overlap. Compatible legacy/deterministic rates retain grounded hours, eligibility and payout metadata; explicit new currency/period can fill a compatible unknown. Distinct amount spans remain tiers, and repeated same-clause rates collapse. Incompatible interpretations of the same clause are withheld with `compensation_needs_review`; workspace `needs_review` suppresses confident aggregate display/sorting rather than treating a dispute as missing zero. Ambiguous legacy amount anchors fail conservatively into review. When no usable new pay exists, valid source-current legacy/deterministic pay remains available. Unknown display currency and period stay unknown; sorting-only CAD/missing-zero defaults, owner-approved conversion estimates and display preferences are otherwise unchanged.

The reader makes a fixed number of bulk source-index queries per workspace, not an index scan per posting. Detail reads restrict children to its posting. Source grounding is not a completeness or semantic-accuracy guarantee: previously audited omitted periods and stipend remain extraction evidence limitations; this integration does not invent recoveries. Read-only production/browser acceptance requires restarting an older API safely after extraction finishes.

## Refresh safety

1. Explicit JSON POST only; no refresh on startup, GET, SSE connection, page load, or reconnect.
2. A SQLite transaction reserves one running collection before any login starts. Concurrent requests receive **409**, including requests from another server process.
3. Every finished run enforces a persisted **300-second cooldown**. Requests during cooldown receive **429** with `Retry-After`; they do not queue a login.
4. A fresh browser authentication and runtime capture immediately precede collection. Captured page 1 is reused.
5. Direct listing fetches remain sequential in batches of at most three, with a **2-second completion-to-next-start interval**. Existing transient retry limits remain two retries after **4/8 seconds**; fatal errors stop.
6. Only a completed listing pass is saved. Then the listing-derived detail queue runs sequentially. Each successful plain-text content snapshot is saved atomically with its attempt/revision; later failures preserve completed checkpoints. No application actions, raw HTML, tokens, or cookies are stored.
7. Refresh authentication blocks service workers. The approved refresh lifecycle keeps the browser open with background egress denied through complete `ListingCollection.run()` and the `save_listings()` commit. Inside the first durable detail attempt, context-wide routing then permits at most two distinct observed title-read POST forms on the exact board URL and original frame/ID. Real responses are fetched with zero implicit redirects/retries; metadata is fulfilled to dispatch dependent markup, while validated final markup is cached without rendering and its original request stays pending until browser closure. No responses or opaque actions are invented. Every actual bootstrap request intent is recorded before network access using the shared detail burst budget. After sanitized markup capture the browser closes promptly; first content is reused without refetching, and remaining details use sequential direct HTTP. GET image/font/stylesheet/media/texttrack/script requests are aborted without transport or capture invalidation, even during an active fetch or after capture completion; they never enter templates, the request callback, or the ledger. Script GETs are aborted, never fetched, fulfilled, or executed by this guard. Document/navigation, xhr/fetch, and non-GET requests receive no exemption. Unexpected/duplicate/alternate detail requests, frames, forms, actions, and overlaps remain fatal; an invalid gate stays invalid. Blocked-request diagnostics contain only fixed resource/method/phase/disposition classes and per-class counts saturated at 100,000; inspection/abort failures remain fatal without exposing source values. Failure preserves saved listings/checkpoints and never automatically relogs in. A cap of 12 normally uses 13 actual requests (metadata plus markup plus 11 remaining), or 12 if markup alone suffices, excluding allowed natural retries. Bounded live verification remains required.

Detail attempts (including retries and initial template classification) use bursts of **10**, with **0.6–1.0 seconds** after each burst and an additional **1–2 seconds** every fifth burst. Sleep only occurs before a next attempt; there is no concurrency or final pause. Initial request plus three transient retries wait **5/10/15 seconds** from failure completion. Retry and burst deadlines both apply (their maximum, not a shortened pause). HTTP 408/500/502/503/504 and transient network failures may retry; 429, authentication, redirects, TLS and validation failures stop. Valid server `Retry-After` is persisted and extends the normal cooldown; nothing resumes automatically.

`WW_DETAIL_LIMIT=12` is a **server-owned verification cap**, never a client parameter. It limits postings, not HTTP attempts: first-posting metadata classification may add one request; retries add attempts. Normal runs process the full current queue. Capped totals and `detail_limited` are streamed honestly. Invalid cap values fail startup. Metadata is no longer requested once markup is identified; only `content_only` data is persisted.

The UI keeps the refresh button clickable during an already-running run or cooldown and explains the refusal with a top-centered toast, without sending another POST when the current status already establishes refusal. Offline/in-flight guards remain disabled; the API still enforces 409/429 for races. No cooldown countdown or automatic retry is shown.

`RefreshSnapshot.eta_seconds` is nullable and comes only from the collector's reviewed emitted duration argument. It is the latest estimate, not a client countdown. Unestimated stages, retry waits, saving, and completed states have no ETA. The frontend separately renders an approximate countdown at one displayed second per 1,200 ms, resets to every completed page's latest estimate (including higher or identically rounded estimates), and shows “Updating ETA…” when the estimate expires. The next tick is anchored to the new sample; ordinary renders do not reset it. Detail stages always use API `eta_seconds=null`. Independently, the frontend shows “Fetching posting details” and estimates remaining time from local completed-detail deltas over elapsed time (including rests/retries), sampled every second. It warms up for at least two observed completions and two seconds, resets on run/phase entry, counter regression or total changes, and starts a new baseline after reload without persistence. The estimate is recalculated during stalls rather than counted down; it is approximate, not a completion guarantee. Detail counters are `details_completed`, `details_total`, `detail_requests`, `detail_retries`, `detail_bursts`, and `detail_wait_seconds`, plus `detail_limited`. The frontend changes its progress scale and reloads saved listings once when details begin or the run fails. The progress bar displays the stage and counts; sanitized log details appear only in a compact expandable console. This console hides three seconds after an observed run ends and stays hidden for historical terminal runs on initial load.

A crashed process can leave a durable `running` collection and content attempt. Restore exposes persisted content progress but never replays unfinished attempts. The API deliberately does not reclaim it automatically, because another process could still be active. Confirm no worker/browser is running before manually recovering that run in SQLite. Until then, new refresh requests stay blocked. Shutting down during a live refresh is not a supported cancellation mechanism.

## Privacy

Every emitted refresh-stage log is forwarded to the console with its scope, level, reviewed message, and safe arguments, rather than replaced with generic stage summaries. This includes authentication/navigation steps, batch/page counts, ETA, retries, and warnings/errors. DEBUG entries are included when enabled by `WW_LOG_LEVEL=DEBUG`. A reviewed format/text allowlist redacts unknown arguments and withholds unreviewed messages; a regression test requires review of newly added stage log formats. Duo codes, usernames/passwords, request actions, cookies, response bodies, and raw exceptions are excluded. The frontend should display server-supplied errors only through bounded safe messages, never debug dumps. Workspace responses use `Cache-Control: no-store`; the browser must not persist postings, logs, or authentication data in local storage. The frontend persists only the light/dark theme preference (`heroui-theme`). Logs stay in memory, bounded to 200 entries; the console does not pull a reader back to the bottom after they scroll up.

The API rejects unexpected Host headers. Refresh requests reject unapproved browser origins and require `application/json`; approved origins are localhost/127.0.0.1 on ports 5173 and 8787. Bind only to loopback. There is no remote-user authentication system. Frontend dev/preview scripts also bind to loopback by default. Vite denies direct filesystem access to credentials, private experiments, the SQLite data directory, and Pi state, in addition to its standard secret-file exclusions.

## Contract generation

```sh
uv run --project backend --locked python -m waterlooworks_flash_next.openapi docs/openapi.json
npx openapi-typescript docs/openapi.json -o frontend/src/lib/api.generated.ts
uv run --project backend --locked python -m waterlooworks_flash_next.openapi docs/openapi.json --check
```

Schema generation imports contracts only: it does not open the database or load credentials. The generator uses a root-level TypeScript 5 tool dependency because `openapi-typescript` requires it; the frontend remains on TypeScript 6 in its workspace.

Ordinary tests exercise real temporary SQLite/API responses, documentation, SSE framing, origin checks, and refresh-claim/cooldown logic without authenticating or inventing postings. Live verification is explicit and bounded to one refresh; aggregate results are recorded in the scraping workflow document.

### Read-only stored job page

`GET /api/postings/{posting_id}` (`getPosting`) accepts ASCII digit-string IDs without numeric conversion (including leading zeros). It uses a parameterized query on a read-only SQLite connection and returns the current allowlisted listing plus `content` (`content_only` fields) and `content_observed_at`. A known listing without content has both fields null; an unknown ID returns 404. Invalid IDs return sanitized 422 errors. No request starts authentication, refresh, content retrieval, or application actions. No internal attempts or session metadata are exposed.

Title links open `?job=<ID>` within the existing workspace; browse parameters remain intact. Back restores listing focus and scroll, and modifier clicks retain native new-tab behavior. Direct links and reloads read saved content only.

The three analysis reads are also GET-only and use read-only SQLite connections. A deterministic projection is returned only while its listing/content hashes and latest revision identities match current saved source. History contains actual observations rather than interpolated samples and labels legacy/fallback timestamp provenance. Comparisons exclude the selected job and require an exact shared recognized skill, level, or work term. Salary comparison is limited to one known native currency/period pair from the selected job and returns exact fixed/range/open/tier bounds; it performs no midpoint, FX, period, hours, or weeks inference. Missing values remain unavailable rather than zero. Only current-source, current-configuration, evidence-validated model results are displayed; rejected outputs remain unavailable. Validation acceptance is not a claim of comprehensive extraction coverage.

Manual `POST /api/refresh` now runs collection, deterministic normalization, then bounded local extraction over all saved postings with detail content. It holds the existing durable single-flight claim until local analysis terminates. The snapshot exposes `analysis_job_id`, `analysis_total`, `analysis_completed`, `analysis_accepted`, `analysis_rejected`, `analysis_failed`, `analysis_skipped`, and `analysis_cache_hits`. Safe console logs cover normalization, queue/model identity, progress counts, and completion/failure; source bodies and reasoning are never logged. The inference job UUID equals the refresh UUID so restart recovery can restore counts without resuming work. A provider failure preserves collected data and uses the existing `internal` terminal error category with a specific local-analysis log message. Startup, GET, and SSE reconnect never start collection or inference.

### Targeted disciplines

`GET /api/workspace` includes `targeted_disciplines: string[]` on each posting, projected in one batched read from saved content. Missing values yield `[]`; whitespace, duplicates, and legacy widget marker lines are removed. Full content is not added to the workspace response. The frontend tolerates an older endpoint omitting this field during deployment. Repeated `target` URL parameters retain exact source leaf identities; selection is OR within this filter and AND across filters. No country or theme/degree equivalence is inferred, and no collection or backfill is needed.
