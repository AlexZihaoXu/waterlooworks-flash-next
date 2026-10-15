# Agent API

A bearer-token API that lets tools such as AI agents search, filter, read and mark saved job postings **on behalf of one signed-in app user**, and check when the saved listings were last refreshed and when the next scheduled refresh is due. It is read-only apart from the caller's own posting marks: **there is no endpoint that starts, schedules or configures a refresh, and no admin setting is reachable.** A test asserts the exact route inventory.

Every route lives under `/api/agent/v1/` and is documented in the tracked [`openapi.json`](openapi.json) (tag `agent`, security scheme `AgentToken`). Schema18 adds the `api_tokens` and `posting_marks` tables.

## Live docs and schema

No token is needed to read these; they describe the API, not anyone's data:

- `https://<your-host>/docs/`: interactive Swagger UI. Click **Authorize**, paste your token (kept in that tab's memory only) and use **Try it out**.
- `https://<your-host>/api/agent/v1/openapi.json`: the machine-readable OpenAPI 3 schema of the agent routes only, with this guide as its description. Point an agent here to learn the API.
- `https://<your-host>/api/agent/v1/guide`: this guide as Markdown.

## Getting a token (web app settings)

Each approved user (member or admin) manages one personal token from the account/settings area of the web app, which calls these session-cookie routes (same Origin/JSON/`X-WW-Request: 1` rules as every other session mutation; all responses are `no-store`):

| Method | Route                                     | Result                                                                                                                                            |
| ------ | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/account/api-token`                  | `{configured, hint, created_at, last_used_at}`; never the token                                                                                   |
| POST   | `/api/account/api-token/reset`            | Body `{}`. Mints a new token and returns `{token, hint, created_at}` **once**; the previous token stops working immediately. At most 5 per 10 min |
| DELETE | `/api/account/api-token`                  | Body `{}`. Revokes the token immediately; returns `{configured: false, …}`                                                                        |
| GET    | `/api/account/posting-states`             | `{marks: {"<posting_id>": {posting_id, state, viewed_at, updated_at}}}`                                                                           |
| PUT    | `/api/account/posting-states/{id}`        | Body `{state}` where `state` is `interested`, `applied`, `not_interested` or `null` (clear); 404 for unknown IDs                                  |
| PUT    | `/api/account/posting-states/{id}/viewed` | Body `{viewed: true}` keeps the first view time; `{viewed: false}` clears it                                                                      |

The token is **show-once**: the server stores only a SHA-256 digest and a hint like `wwf_…ab12`. If it is lost, reset it. One active token per user; a reset or revoke invalidates the old value on the next request. `last_used_at` is recorded at most once per minute.

## Authentication

Send the token as a bearer credential on every request:

```sh
curl -sS https://<your-host>/api/agent/v1/me \
  -H 'Authorization: Bearer wwf_…'
```

- Only the `Authorization: Bearer` header is consulted. Cookies are ignored and query-string tokens are never accepted.
- The token is re-resolved on **every** request to a current, approved app user and role; a revoked or replaced token, or a user who lost access (pending, declined or removed in the approval tree), gets `401` immediately.
- Any missing, malformed, unknown, replaced or revoked token answers a generic `401` with `WWW-Authenticate: Bearer` and the fixed body `{"detail": "Agent token was not accepted."}`. Tokens are never echoed or logged.
- The usual transport guards still apply: the configured `Host`, HTTPS (or the explicit loopback-HTTP development exception), and `Content-Type: application/json` with a body of at most 20 000 bytes on `PUT`. Because bearer requests ignore cookies, the session CSRF headers (`Origin`, `X-WW-Request`) are **not** required on agent routes. If a browser-originated request does carry an `Origin` header it must still be an allowed one.
- Responses are `Cache-Control: no-store`.

### Rate limit

At most **60 requests per minute per token** (fixed window). Exceeding it returns `429` with `Retry-After` (whole seconds). Token resets are separately limited to 5 per 10 minutes per user.

## Endpoints

| Method | Route                                | Result                                                                                                                     |
| ------ | ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/agent/v1/me`                   | `{uid, role, profile}`; `profile` is `{display_name, email, photo_url}` or `null` when the directory lookup is unavailable |
| GET    | `/api/agent/v1/refresh`              | Read-only refresh facts (below)                                                                                            |
| GET    | `/api/agent/v1/postings`             | Search, filter, sort and page the saved postings with the caller's marks                                                   |
| GET    | `/api/agent/v1/postings/{id}`        | One posting with its plain-text detail content and analysis                                                                |
| PUT    | `/api/agent/v1/postings/{id}/state`  | Body `{state}` (`interested`, `applied`, `not_interested` or `null`)                                                       |
| PUT    | `/api/agent/v1/postings/{id}/viewed` | Body `{viewed: true}` or `{viewed: false}`                                                                                 |

Admins and members get the same member-visible data: no admin-only field (refresh capability, Duo challenge, pacing, credentials, settings, logs) is exposed here.

### Refresh status

```sh
curl -sS https://<your-host>/api/agent/v1/refresh -H 'Authorization: Bearer wwf_…'
```

```json
{
  "running": false,
  "status": "completed",
  "last_refreshed_at": "2026-10-12T05:31:08.412771+00:00",
  "last_run_finished_at": "2026-10-12T05:48:02.114530+00:00",
  "next_scheduled_at": "2026-10-13T11:00:00+00:00"
}
```

- `last_refreshed_at` is when the saved listings were last observed (the same value behind the web header's "Refreshed … ago"); `null` before any refresh.
- `last_run_finished_at` is the finish time of the most recent run of any outcome; `status` is the current/last run status (`idle`, `running`, `completed`, `failed`, `interrupted`).
- `next_scheduled_at` is `null` when the admin schedule is off, has no times, or scheduling is inactive on this server. Agents cannot change any of this.

### Searching postings

All query parameters are optional. Repeat a parameter to pass several values (`?state=interested&state=applied`). Unknown parameters are refused with `422`. Filters are **OR within a parameter and AND across parameters**, mirroring the web app.

| Parameter                | Meaning                                                                                                                                                                                                                                                                                                    |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `q`                      | Keyword search (≤200 chars). Every whitespace-separated word must appear, case-insensitively, in some text field: posting ID, title, organization, location, or the accepted inferred country name/code. Words may match different fields                                                                  |
| `organization`           | Exact organization names (case-insensitive), up to 50                                                                                                                                                                                                                                                      |
| `city`                   | Exact stored location values, up to 50                                                                                                                                                                                                                                                                     |
| `country`                | Accepted inferred country names or ISO codes, up to 50. Postings without an accepted inference never match                                                                                                                                                                                                 |
| `discipline`             | Exact targeted-discipline source values, up to 50                                                                                                                                                                                                                                                          |
| `arrangement`            | `Remote`, `Hybrid`, `In-person`                                                                                                                                                                                                                                                                            |
| `state`                  | Caller's state: `interested`, `applied`, `not_interested`, `none` (unmarked)                                                                                                                                                                                                                               |
| `exclude_not_interested` | `true` drops postings the caller marked `not_interested` (default `false`)                                                                                                                                                                                                                                 |
| `viewed`                 | `true` only postings the caller marked viewed; `false` only unviewed; omit for both                                                                                                                                                                                                                        |
| `sort`                   | Web sort key: `id` (default, numeric posting ID), `newest`, `remaining` (deadline, soonest first; passed then unknown), `applications-desc`, `openings-desc`, `organization`, `compensation-average`, `compensation-minimum`, `compensation-maximum`. Unknown values sort last; ties fall back to ID order |
| `page`, `page_size`      | 1-based page and size (default 20, maximum 100)                                                                                                                                                                                                                                                            |

Compensation sorts rank the stored source-grounded pay summary within currency/period cohorts without currency conversion (the web app additionally estimates hourly equivalents and approximate FX for display); daily rates and disputed pay are unrankable and listed last.

```sh
curl -sS 'https://<your-host>/api/agent/v1/postings?q=python&arrangement=Remote&state=none&sort=remaining&page_size=50' \
  -H 'Authorization: Bearer wwf_…'
```

```json
{
  "page": 1,
  "page_size": 50,
  "total": 2,
  "total_pages": 1,
  "postings": [
    {
      "posting_id": "…",
      "title": "…",
      "organization": "…",
      "division": null,
      "location": "…",
      "openings": 2,
      "level": "…",
      "application_count": 14,
      "deadline": "Oct 15, 2026 11:59 PM",
      "work_arrangement": "Remote",
      "country_code": "CA",
      "country_name": "Canada",
      "targeted_disciplines": ["…"],
      "compensation": {
        "currency": "CAD",
        "period": "hour",
        "minimum": "…",
        "maximum": "…",
        "average": "…",
        "…": "…"
      },
      "first_seen_at": "…",
      "last_seen_at": "…",
      "state": null,
      "viewed_at": null
    }
  ]
}
```

`openings`, `level`, `application_count` and `deadline` are the source values exactly as stored (numbers or text); `deadline` is Eastern Time text. `country_code`/`country_name` come only from an accepted model inference and are never derived from the city. `compensation` is the same `CompensationSort` summary the web cards use, or `null`.

### Reading one posting

```sh
curl -sS https://<your-host>/api/agent/v1/postings/<id> -H 'Authorization: Bearer wwf_…'
```

Returns every summary field above plus:

- `content`: the saved plain-text detail fields (`{id, coverage, fields: {"Job Summary:": "…", …}}`) or `null` before detail collection; `content_observed_at` is when they were saved.
- `analysis`: the same `PostingAnalysisResponse` as `GET /api/postings/{id}/analysis` (source facts, reconciled pay, keywords, skills, application route and extraction status).
- `state` and `viewed_at`: the caller's marks. **Reading never marks a posting viewed**; only the explicit `viewed` route does.

Unknown IDs return `404 {"detail": "Stored posting not found."}`; malformed IDs return a fixed `422`.

### Marking postings

```sh
curl -sS -X PUT https://<your-host>/api/agent/v1/postings/<id>/state \
  -H 'Authorization: Bearer wwf_…' -H 'Content-Type: application/json' \
  -d '{"state": "applied"}'

curl -sS -X PUT https://<your-host>/api/agent/v1/postings/<id>/viewed \
  -H 'Authorization: Bearer wwf_…' -H 'Content-Type: application/json' \
  -d '{"viewed": true}'
```

Both answer with the resulting mark `{posting_id, state, viewed_at, updated_at}`. States are per user and per posting; absence means no state. `{"state": null}` clears the state, `{"viewed": false}` clears the view time, and a mark with neither is deleted (`updated_at` becomes `null`). Marks set here are the same marks the web app shows and edits. Validation failures return a fixed `422` that never echoes the submitted value.

## Privacy and limits

- The plaintext token exists only in the reset response. Store it in a secret manager, never in a repository, prompt log or shared document.
- Agents see exactly what the owning user can see as a member of the app, and only that user's marks. Other users' marks are never readable.
- Nothing on this API contacts WaterlooWorks or any model provider; every read is served from the saved SQLite workspace.
- Access removal follows the app's admission rules: a token stops working as soon as its user is no longer approved, and removal by the administrator deletes the token outright.
