# Local listings API

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

| Method | Route                 | Result                                                                         |
| ------ | --------------------- | ------------------------------------------------------------------------------ |
| GET    | `/api/health`         | Service status and SQLite schema version                                       |
| GET    | `/api/workspace`      | Stored listings and their revision histories; never triggers external fetching |
| GET    | `/api/refresh`        | Current progress and bounded sanitized logs                                    |
| POST   | `/api/refresh`        | Explicit `{}` JSON body; 202 with run ID if accepted                           |
| GET    | `/api/refresh/events` | SSE events named `refresh`; data conforms to `RefreshSnapshot`                 |

`?once=true` returns one SSE snapshot and closes, for clients testing the stream format. Normal streams send current state on connection and changes thereafter, with keep-alive comments. Reconnection never starts collection. Event payloads are documented in OpenAPI using the `RefreshSnapshot` schema and an `x-event-payload` reference.

The workspace endpoint is a read-only local snapshot, not a live WaterlooWorks request. It currently returns all stored listings/history for client-side filtering and pagination. Larger histories will require server-side paging later. Country remains null until verified extraction exists. Individual posting detail collection is **not included**.

## Refresh safety

1. Explicit JSON POST only; no refresh on startup, GET, SSE connection, page load, or reconnect.
2. A SQLite transaction reserves one running collection before any login starts. Concurrent requests receive **409**, including requests from another server process.
3. Every finished run enforces a persisted **300-second cooldown**. Requests during cooldown receive **429** with `Retry-After`; they do not queue a login.
4. A fresh browser authentication and runtime capture immediately precede collection. Captured page 1 is reused.
5. Direct listing fetches remain sequential in batches of at most three, with a **2-second completion-to-next-start interval**. Existing transient retry limits remain two retries after **4/8 seconds**; fatal errors stop.
6. Only a completed listing pass is saved. No per-posting detail requests, application actions, raw responses, tokens, or cookies are stored by refresh.
7. The browser closes after the first listing request/session is captured and before direct HTTP collection starts. The copied request/cookies remain runtime-only; there is no logout request. Failure cleanup remains as a fallback. No automatic relogin/restart occurs on failure.

The UI keeps the refresh button clickable during an already-running run or cooldown and explains the refusal with a top-centered toast, without sending another POST when the current status already establishes refusal. Offline/in-flight guards remain disabled; the API still enforces 409/429 for races. No cooldown countdown or automatic retry is shown.

`RefreshSnapshot.eta_seconds` is nullable and comes only from the collector's reviewed emitted duration argument. It is the latest estimate, not a client countdown. Unestimated stages, retry waits, saving, and completed states have no ETA. The frontend separately renders an approximate countdown at one displayed second per 1,200 ms, resets to every completed page's latest estimate (including higher or identically rounded estimates), and shows “Updating ETA…” when the estimate expires. The next tick is anchored to the new sample; ordinary renders do not reset it. The progress bar displays the stage and counts; sanitized log details appear only in a compact expandable console. This console hides three seconds after an observed run ends and stays hidden for historical terminal runs on initial load.

A crashed process can leave a durable `running` collection. The API deliberately does not reclaim it automatically, because another process could still be active. Confirm no worker/browser is running before manually recovering that run in SQLite. Until then, new refresh requests stay blocked. Shutting down during a live refresh is not a supported cancellation mechanism.

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
