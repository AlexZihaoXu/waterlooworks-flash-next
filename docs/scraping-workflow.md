# Guided scraping workflow

## Development approach and privacy

Explore WaterlooWorks in an interactive uv-managed Python shell in `.experiment/test/`, following user instructions. This is the actual platform flow, not a mock or unit test. Unit tests cover credential-free logic only; live authentication requires explicit user direction.

Never view the credentials file through agent tools, expose username/password, or collect login screenshots, traces, HTML dumps, or sensitive network details. The user authorized printing the Duo verification code to their private console; do not share that output or commit it.

## Verified findings

- Credential entry is required again even with the saved browser profile.
- Sign in can lead to a Duo code challenge. The code may appear after the prompt; DOM-only polling successfully detected it and logged it once.
- Duo can show **Request timed out / Push timed out**. A fresh-profile run verified automatic detection, ERROR, three-second wait, browser closure, and exit 1 without retry.
- Entering a wrong code in Duo Mobile produced **Invalid verification code**, alongside **Try again** and **Other options**. The new handler was then verified on that same invalid-code page: ERROR, three-second wait, browser closure, and exit 1 without retry. It runs before code detection on every observer poll; the whole login was not repeated for this handler check.
- A fresh-profile approval run verified the complete automatic path: code detected → user approval → **Is this your device?** → **Yes, this is my device** clicked automatically → WaterlooWorks confirmed.
- On a subsequent login using that profile, credential submission reached WaterlooWorks without another manual Duo approval. The user observed a brief Duo transition; the script must not require catching that transient URL.
- The destination had **Dashboard**, **Logout / Log out**, and **Co-op** visible on `waterlooworks.uwaterloo.ca`. The script requires the HTTPS host plus Dashboard and logout controls before declaring arrival.

The challenged-login path with automatic device trust and the saved-trust path have both passed end-to-end runs. An HTTP-cache-only reset preserved device trust, while deleting the dedicated profile restored the Duo challenge. Later job-navigation branches remain placeholders.

Still not live-tested: approval without the device-trust prompt, ten checks with no code, the 60-second transition timeout, and an unknown destination. These defensive branches are implemented, not claimed verified.

## Playwright flowchart

Navigation steps add **250 ms**. Post-submission observation runs every **500 ms**, reading only the existing DOM. Decision arrows add no extra delays.

```mermaid
flowchart TD
    A[Visible Chromium: private persistent profile, cache enabled]
    A -->|250 ms| B[Viewport 1024 x 768]
    B -->|250 ms| C[Open WaterlooWorks]
    C -->|250 ms| D[Wait for landing page load]
    D -->|250 ms| E[Click Students/Alumni/Staff]
    E -->|250 ms| F[Wait for ADFS page and username field]
    F -->|250 ms| G[Load and type username]
    G -->|250 ms| H[Next; wait for password field]
    H -->|250 ms| I[Load and type password]
    I -->|250 ms| J[Password ready]
    J -->|250 ms| K[Click Sign in exactly once]
    K -->|500 ms| L{WaterlooWorks host plus Dashboard and logout?}
    L -->|Yes: challenge completed or trusted-device redirect| DONE[Log confirmed WaterlooWorks arrival]
    DONE -->|250 ms| BOARD[Start passive capture; open board URL; wait for load]
    BOARD -->|250 ms| ALL[Find All Jobs button at runtime and click]
    ALL --> CAPTURE[Reuse latest captured page 1; do not require a new request]
    CAPTURE --> PAUSE[Pause; listing collection is explicit opt-in]
    CAPTURE -->|Caller starts collection immediately after fresh login| BATCH[Sequential batch of at most 3 listing pages]
    BATCH --> SEED{Captured page 1?}
    SEED -->|Yes| REUSE[Reuse captured response; no replay]
    SEED -->|No| WAIT[Wait until 2 seconds after previous completed fetch]
    WAIT --> FETCH[One direct POST for next listing page]
    FETCH --> VALID{Valid response and requested page?}
    VALID -->|No| RETRY{Transient error and fewer than 3 attempts?}
    RETRY -->|Yes: 4s then 8s after failed completion| FETCH
    RETRY -->|No| STOP[Raise safe error; caller must close browser and return failure]
    VALID -->|Yes| STORE[Deduplicate by posting ID]
    REUSE --> STORE
    STORE --> PROGRESS[Log page/count/unique/duplicates/ETA]
    PROGRESS --> MORE{More pages in initial page bound?}
    MORE -->|Yes: same delay across batch boundaries| BATCH
    MORE -->|No| LISTDONE[In-memory listing collection complete; no details]
    LISTDONE --> EXPORT{JSON export requested?}
    EXPORT -->|Yes| JSON[Atomically save allowed listing fields to ignored output directory]
    EXPORT -->|No| MEMORY[Keep results in memory only]
    L -->|No| M{Known HTTPS authentication host?}
    M -->|No| UNKNOWN[Pause for guidance; do not assume success]
    M -->|Yes| N{Duo timeout or Invalid verification code visible?}
    N -->|Yes| ERROR[Log ERROR]
    ERROR -->|3 seconds| EXIT[Close browser; exit 1]
    N -->|No| O{Optional device trust prompt visible?}
    O -->|Yes, not already accepted| TRUST[Click Yes, this is my device once]
    TRUST --> TRANSITION[Allow up to 60 seconds for transition]
    O -->|No| P{On Duo awaiting code or approval?}
    P -->|No| TRANSITION
    P -->|Yes| Q{Unique visible three-digit code?}
    Q -->|Yes| CODE[Log code once; wait for user approval]
    CODE -->|500 ms, DOM only| L
    Q -->|No, code previously seen| TRANSITION
    Q -->|No, never seen| LIMIT{10 code checks exhausted?}
    LIMIT -->|Yes| ERROR
    LIMIT -->|No: 500 ms| L
    TRANSITION --> T{Transition deadline exceeded?}
    T -->|Yes| ERROR
    T -->|No: 500 ms| L
```

## Branch behavior

- A returning trusted-device login can pass briefly through Duo or reach WaterlooWorks before polling sees Duo. Neither case requires a visible code or another push.
- On Duo, before a code has been seen, allow 10 unsuccessful checks at 500 ms (approximately five seconds plus DOM-query time). A code candidate must be the only visible three-digit text value while the observed verification-code prompt is visible. This conservative strategy worked live; ambiguous matches fail closed.
- Before inspecting codes or clicking device trust on each poll, check for **Invalid verification code**, **Request timed out**, or **Push timed out**. Log a fixed ERROR reason, wait three seconds, close the browser, and exit 1. No submitted code value or raw error is logged; never retry.
- Once the code is detected, log it once and continue DOM-only observation while the user approves it. Duo's own timeout UI determines challenge expiry. A disappearing code is not proof of success: continue observing redirects or the optional device prompt.
- **Is this your device?** is optional. When both heading and **Yes, this is my device** button are visible on the verified Duo origin, click once as user-authorized, then wait for WaterlooWorks. This action can send a trust-confirmation request; it is not a poll or a new push.
- Known-host transitions have a 60-second bound, reset while a visible code awaits approval and after accepting device trust. Timeout or exhausted code detection closes after three seconds and exits 1.
- Unknown destinations pause for guidance. No automatic retry, reload, fetch, extra navigation, or additional Duo push is performed by polling. The page itself may perform normal background authentication requests.

## Job-board navigation

Observed manual path: **Toggle Main Menu → Co-op Jobs → View Full Cycle Service Job Board**. The resulting URL was `https://waterlooworks.uwaterloo.ca/myAccount/co-op/full/jobs.htm`, with no query or fragment. Refresh preserved the URL and visible Job Board/Search markers.

After confirmed dashboard arrival, the script now navigates directly to that observed route, waits for page load, then finds the **All Jobs** button by its accessible name and clicks it. All Jobs is a runtime action, not a URL-derived link. Each step adds 250 ms. The My Program filter is left untouched. Listing-response capture starts **before navigation**, because the initial board load already fetches the list and the All Jobs click may send no new request. After the single click, wait for outstanding board XHR/fetch activity to finish and a short 250 ms quiet interval, then reuse the latest matching response in memory. Never click again or replay page 1 just to obtain a response.

A saved-profile end-to-end rerun verified the direct dashboard-to-board shortcut and runtime All Jobs click. The browser remained open afterward for guided exploration; a later inspection identified the list POST and verified direct single-page fetches. The initial batching test completed seven pages with 350 unique postings and zero duplicates, then failed on page 8. The original sanitized error did not identify its cause. The first fresh-login retry run stopped before collection: it waited for a response after clicking All Jobs, but the listing had already loaded during navigation. A passive diagnostic confirmed the click completed and all observed board XHRs finished before it. Capture was moved before navigation and passed the next live run. That run then isolated the page-8 failure as JSON/encoding validation rather than transport.

### Single-posting retest status

The latest single-posting retest **passed** after fresh trusted-device login and page-1 capture. Two earlier attempts stopped before making detail requests because two visible anchors shared the exact same title. The successful attempt scoped the title anchor to a row containing the captured posting ID, resolving one unique target.

One title click generated two POSTs to the board route, each carrying runtime `action` and `postingId` fields:

- **HTTP 200 JSON metadata:** expected metadata keys were present and its `id` matched the selected posting.
- **HTTP 200 markup:** nonempty markup contained the selected posting title.
- **One direct Python markup replay:** HTTP 200, with the selected title present again. The opaque action and current cookies stayed in memory. Redirects were forbidden; a 2-second pause preceded the direct request, and no retry was needed.

Browser and direct markup were **not textually identical**. This verifies retrieval of title-bearing detail markup, not complete field extraction or exact response equivalence. The metadata request was not separately replayed through direct HTTP. No full-board collection, application action, export, or database write occurred. The browser closed and the private diagnostic was removed. Playwright emitted `Target closed` asynchronous cleanup warnings after the successful checks; these did not prevent the fetches but remain a cleanup caveat. No reusable detail collector is implemented yet.

## Listing collection: sequential and opt-in

The observed listing request is a form-encoded POST to the job-board route with `page`, `itemsPerPage`, `sort`, `filters`, `columns`, `keyword`, `action`, and `isDataViewer`. Response content is JSON despite its HTML content type: `totalResults`, `page`, and `data`. Rows contain key/value cells, including posting ID, title, and organization. The opaque action and authenticated cookies are captured dynamically; never log, hardcode, or persist them.

Direct page 1 and page 5 requests succeeded during exploration. Page 1 counts and totals matched the captured browser response, but full record equality did not; equivalence was not claimed. Page 5 returned 50 records. The parser also validated the existing page-1 response and 50 unique IDs offline, without another network request.

A fresh-login, single-request page-8 diagnostic returned HTTP 200 and 50 records: the response was not valid UTF-8 but was valid JSON when decoded as its declared **ISO-8859-1** charset. Both browser-captured and direct responses now prefer strict UTF-8 decoding (including BOM), falling back to declared ISO-8859-1/Windows-1252 only when UTF-8 fails. No replacement characters, body logging, or charset guessing is used. JSON syntax and schema validation remain separate fail-closed checks. Codec tests contain no posting fixtures or authentication data.

The corrected fresh-login collection passed end-to-end: **25 pages, 1,216 records, 1,216 unique posting IDs, zero duplicates**. The last page contained 16 records. Page 1 was reused from browser capture; pages 2–25 were fetched sequentially, in batches of at most three with the 2-second minimum interval. No detail requests were made. Retry exhaustion and transient-retry behavior remain policy-tested rather than live-failure-tested.

Callers must perform a fresh login, capture the runtime action/cookies and page 1, then start `ListingCollection.run()` immediately. The temporary execution scripts have been removed; the reusable core does not start a collection automatically. Do not reuse an old interactive shell's request template: sessions may expire quickly. A capture older than 30 seconds is rejected before starting (a conservative guard, not a claim about actual cookie lifetime).

The captured page 1 is reused as the first completed fetch, then **one direct Python HTTP POST runs at a time**. Pages are grouped into batches of **at most three**, not fetched concurrently. There is a minimum **2-second completion-to-next-start interval** after every fetch, including between batches. Time spent processing results counts toward that interval; there is no additional batch cooldown. The browser bootstrap itself may make its normal UI requests; the pacing governs subsequent collector requests.

Every completed page logs page/total, rows received, cumulative records, unique IDs, duplicates, and ETA. ETA is a rolling estimate from observed request durations plus remaining 2-second intervals, assuming no further retries; it is not a guarantee. Page 1 is explicitly marked as a captured response rather than a new request.

The initial `totalResults/itemsPerPage` determines the bounded pass. If totals change, log a warning without expanding the crawl indefinitely. Deduplication uses posting ID; it cannot prevent omissions if a live list changes during pagination. Retry a page at most **twice** for network/timeouts, interrupted responses, or HTTP 408/500/502/503/504: initial attempt after the normal **2 seconds**, then retries **4 seconds** and **8 seconds** after the respective failed attempt finishes. Do not stack an extra normal delay on a retry. Stop after the third failed attempt.

Authentication failures (401/403), rate limits (429), redirects, TLS failures, invalid JSON/encoding, schema mismatches, unexpected empty pages, or a page adding no new IDs are not blindly retried. No automatic relogin or detail requests. Logs include only safe category, page, attempt/maximum, numeric HTTP status when available, and retry decision—not exception text, response bodies, or headers.

Results remain only in `listing_collection.postings`. The collector raises a sanitized error and retains partial results in memory. Its caller must close the browser and propagate failure (a future CLI should exit nonzero). The removed runner previously implemented this boundary; the library does not terminate its caller on listing failures. Partial results are lost when the process exits. `complete` means the bounded page pass finished, not that the changing platform was captured atomically. A source is single-use to prevent accidental replay. There is no partial checkpoint/resume yet. An optional completed-pass JSON export is available; no fetched data or mock fixtures are committed. Do not print or serialize the collector or its runtime source.

## Reusable core API

Temporary launchers and execution scripts have been removed. Core modules under `backend/src/waterlooworks_flash_next/` remain:

- `login.py`: `start_login(credentials_path)` creates the persistent browser and enters credentials, returning a `LoginSession` at the password checkpoint.
- `duo.py`: `submit_and_observe_duo(session)` submits once and observes code/trust/failure branches until authenticated arrival or an unknown destination. **Calling it may trigger a Duo notification.** Its existing Duo failure handler closes the browser and exits 1; the refresh worker catches failures and finalizes the run safely.
- `jobs.py`: `open_all_jobs(session.page)` confirms authentication, navigates to the board, clicks All Jobs, and returns a fresh `ListingSource` or `None`.
- `listings.py`: construct `ListingCollection(source)` and immediately call `.run()` for paced, sequential collection. Inspect safe aggregate values such as `.pages_completed`, `.complete`, and `len(.postings)`. Catch `ListingFetchError` at the application boundary and close the session; never log raw exceptions.
- `export.py`: `export_listings(collection, output_directory)` exports a completed collection only. Use a private ignored directory such as `.experiment/output/`.
- `logging.py`: safe stage-log formatting and logger configuration.
- `database.py`: SQLite schema v3, transactional migration, empty run creation, the `StoredListing` model, and atomic `save_listings()` upserts with SHA-256 revision history. The explicit API refresh worker now saves completed listing passes and finalizes run counters/status.
- `details.py`, `detail_schema.py`, `detail_storage.py`: strict detail metadata models, null-only unverified content fields, independent detail history, and explicit fetch-attempt lifecycle APIs. See [the posting model](posting-model.md).

Do not auto-run these modules on import. Callers own browser cleanup through `session.close()` on completion, cancellation, or failure. Chromium uses ignored `.experiment/browser-profile/` with normal caching and service workers enabled. Cookies/storage can persist, but the site controls expiration. Do not inspect, log, commit, ship, or concurrently open the profile.

Typing is approximately **33.3 ms per character**. Credentials load only at runtime on the verified authentication origin. Keep `DEBUG` and `PWDEBUG` unset. No per-posting detail collector is implemented.

## Private JSON export

`export_listings()` atomically writes UTF-8 JSON with a unique timestamped filename in the supplied output directory. Exported fields are allowlisted: ID, title, organization, division, openings, city, level, application count, and deadline, plus collection counts and export time. Runtime actions, cookies, request headers, credentials, internal apply controls, and raw response objects are never serialized. Keep outputs private and ignored; incomplete collections cannot be exported as complete results.

The writer is tested with empty data only, without mock postings or live requests. Full-data export has not been live-verified.

## Logs

Format: `[HH:MM:SS] [scope/LEVEL]: message`, local time, scopes `login`, `duo`, `jobs`, `listings`, `shell`. Colors: gray DEBUG, green INFO, yellow WARN, red ERROR, cyan scope. `FORCE_COLOR=1` forces color; `NO_COLOR=1` disables it. `WW_LOG_LEVEL` supports DEBUG, INFO (default), WARN, ERROR.

Authorized code message: `Detected verification code: [NNN] (waiting...)`, using the detected code once. Never log username/password, input values, page dumps, authentication URLs, or raw exceptions. Keep console output private.

## SQLite model and next steps

The [SQLite storage model in README](../README.md#sqlite-storage-model) defines `collection_runs`, latest-snapshot `postings`, and append-only `posting_revisions`. SHA-256 hashes of canonical allowlisted listing JSON identify changes. Repeated unchanged observations only update last-seen metadata; changes and reversions append full snapshots with run references. Timestamps record persistence time, not a claimed server-side change time. Migrated v1 snapshots become marked baselines; unavailable earlier history is not invented. Local database files live under ignored `data/`, separate from browser-profile experiments. Only allowlisted listing fields belong in the database; no runtime action/cookie/request data.

SQLite v3 adds current detail snapshots, independent append-only detail revisions, and per-posting fetch attempts. Verified metadata has a strict storage contract; unverified body fields are null-only placeholders. Failed attempts preserve valid content. One authorized API refresh populated the local database with 1,216 listings and initial listing revisions; detail tables remain empty.

Tests cover generic hashing, empty migrations/rollback, schema/model constraints, and transaction boundaries without invented postings. Initial populated listing revisions were verified; populated historical backfill and detail success/failure cycles remain unverified. Content extraction, detail collection, and analysis remain **placeholders**.

## Local API and frontend verification

Exactly one authorized live API refresh was tested: POST **202**, concurrent duplicate **409**, **34 SSE frames**, completion after **25 pages / 1,216 unique listings**, persisted listing/revision identity checks, and cooldown **429**. It made no individual detail requests. Swagger `/doc` and `/doc/openapi.json` responded successfully. API logs suppress Duo codes and raw exception text; only fixed messages and numeric progress enter SSE.

Subsequent browser checks used only the stored listings: 12-card pagination, required card fields, title search, location/organization Autocomplete, disabled unverified country, mobile layout, light/dark toggle and reload persistence, and a collapsible monospace console. The console initially follows the bottom, preserves a scrolled-up reading position, and resumes through “Jump to latest”; generic DOM tests cover appended-content follow behavior. Individual posting pages/modals/history UI are intentionally absent. Final browser checks recorded zero refresh POSTs, zero WaterlooWorks requests, and zero page errors. No containers were started.
