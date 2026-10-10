# Pi inference (schema 15)

All AI inference — posting extraction (base pay and skills) and work-country
predictions — runs on the Pi harness: pinned `@earendil-works/pi-ai` and
`@earendil-works/pi-agent-core` 1.1.0 inside a disposable Node sidecar
(`inference-sidecar/`). The former Python → OpenAI Responses provider and its
stdin bridge are removed. `@earendil-works/pi-coding-agent` is deliberately
not used: no built-in bash/edit/read tools, no AGENTS.md loading, no skills and
no `~/.pi` state. No Pi plugins or `.pi/` project settings are involved.

## Architecture

```
refresh / CLI ──► inference.py / country_runner.py   (authoritative)
                      │  builds prompts + sections, validates, writes results
                      ▼
                  pi_bridge.PiSession ── JSON lines over stdin/stdout ──► node sidecar
                      ▲  RPC: credential.read/lock/commit/release,           │ one fresh Agent
                      │       ledger.reserve/finalize (per model turn),       │ per posting
                      │       tool.check_pay, submission.check                │
                      └───────────────────────────────────────────────────────┘
```

- **One process per run.** Python spawns `node --permission
--allow-fs-read=<sidecar dir> --no-addons dist/src/main.js run` with a
  scrubbed environment (only `HOME`/`XDG_*`/`TMPDIR` pointing at a fresh empty
  private temp dir, `TZ`, `LANG`, `NODE_ENV`). The permission model denies file
  writes, child processes, workers and native addons; reads are limited to the
  sidecar's own install directory. Stderr is discarded. The temp HOME is
  verified empty and removed after the run. No session files or transcripts
  exist anywhere; everything lives in memory and the process exits.
- **Strict protocol both ways.** The sidecar validates every inbound object
  with exact key sets and bounds; the backend validates every sidecar message
  with `extra="forbid"` pydantic models. Failures are fixed categories; neither
  side echoes values.
- **Fresh agent per posting.** Each posting gets a new `Agent` (new transcript,
  no memory across postings) with only these custom tools:
  `list_sections`, `read_section(name, offset?, limit?)`, `find_span(query,
section?)`, `check_pay(text)` (deterministic backend parser over RPC; pay
  items only) and `submit_extraction` / `submit_country`, whose parameters are
  the backend's strict JSON schema. pi validates arguments against it; the
  backend then runs the existing `validate_extraction` (or the ported country
  validator) during `submission.check`, returning fixed hints so the model can
  correct evidence at most three times. An accepted submission ends the run;
  the backend revalidates the final payload before writing.
- **Prompt shape.** A compact JSON header (request flags, deterministic skills
  and a section index with sizes) is the user message. Postings up to 12,000
  characters are fully inline (fewest turns and tokens); larger postings inline
  the requested sections first and leave the rest to `read_section`/`find_span`.
- **Caps (per posting).** Max turns (default 6), max tool calls (default 12),
  max output tokens, a per-request timeout and a whole-item deadline. Exceeding
  a cap, answering in prose after one nudge, or repeated evidence rejection
  fails only that posting (`response_schema` / `evidence_rejected`).
- **Ledger per model turn.** Before every provider request the sidecar asks
  the backend to reserve an attempt (unique per run, posting input and turn);
  after the response it reports usage and HTTP status for finalization. Budget
  refusal or ledger failure halts the run.

## Access modes

The admin selects one active mode in **Admin settings → AI**.

| Mode             | Transport                                                     | Credential                                              | Cost accounting                                                                                    |
| ---------------- | ------------------------------------------------------------- | ------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `openai_api_key` | pi-ai `openai` provider → `api.openai.com` Responses          | existing schema13 encrypted key                         | USD: local price table for `gpt-5.6-luna`, otherwise pi-ai catalog estimate; failures stay unknown |
| `chatgpt_signin` | pi-ai `openai` provider with OAuth → `api.openai.com`         | Sign in with ChatGPT (manual paste), refreshed by pi-ai | known $0, `pricing_version=subscription`, bounded by requests per run                              |
| `codex_device`   | pi-ai `openai-codex` provider → `chatgpt.com/backend-api` SSE | Codex device-code OAuth, refreshed by pi-ai             | known $0, `subscription`                                                                           |
| `custom`         | `openai-completions` against your base URL                    | optional bearer token                                   | known $0, `custom-free`                                                                            |

**Terms.** Sign in with ChatGPT officially targets open-source and personal
local apps; remotely hosted apps are waitlisted. Codex device login is
unofficial for third-party apps and may break without notice. The UI says so.

### Sign in with ChatGPT (manual paste)

The hosted backend cannot receive the `http://127.0.0.1:1455/auth/callback`
redirect, so the backend ports pi-ai's flow without a callback server: it
creates PKCE (S256), `state` and `nonce`, uses the stable per-installation
device UUID (stored in `ai_settings`) as `ext_agent_host_id`, and returns the
authorize URL. After signing in, the browser lands on the unreachable loopback
address; the admin pastes that full URL. The backend requires the exact
origin/path, matching `state` (constant-time), a `code` and the issued
`client_id`, exchanges the code with `resource=https://api.openai.com/v1`,
requires an ID token and the `chatgpt.tokens.use.direct` scope, and stores
`{access, refresh, expires, clientId, scopes}` encrypted. Login state lives in
process memory, bound to the admin UID (not the cookie, which rotates hourly),
one at a time, 15-minute TTL, one use.

### Codex device login

The backend requests a user code, shows it with
`https://auth.openai.com/codex/device`, and polls in a background thread
(server interval, +5 s on `slow_down`, pending on 403/404 or
`deviceauth_authorization_pending`, 15-minute timeout, cancellable). On
approval it exchanges the code and stores `{access, refresh, expires,
accountId}` (account from the access-token claim).

### Custom OpenAI-compatible server

Admin-only base URL plus optional token. Guards: http(s) only, no userinfo,
query or fragment; the host is resolved and every answer must be allowed —
link-local/metadata (`169.254.0.0/16`, `fe80::/10`), unspecified, loopback,
multicast and this app's container subnet `172.30.87.0/29` are refused;
RFC1918, CGNAT/Tailscale `100.64.0.0/10` and public addresses are allowed. The
backend's `/models` checks connect to the validated IP with the original
`Host`/SNI, never follow redirects, ignore proxy environment and cap bodies.
The sidecar receives the same validated base URL plus the pinned address and
replaces DNS resolution so only that host resolves, to that address.

`/models` parsing understands the strata shape (`loaded`, `context_length`,
`capabilities`, `supported_parameters`, `reasoning.kind/efforts/default/
request_field/disable_with`, toggle models), LM Studio
`max_context_length`/`loaded_context_length`/`state`, vLLM `max_model_len`,
OpenRouter `context_length` and llama.cpp `meta.n_ctx_train`; otherwise the
context window is entered manually. The selected effort is injected verbatim
into the configured request field (`off` sends `disable_with`); returned
reasoning content is ignored and never stored or logged. Custom servers usually
serve one stream at a time (default parallel 1).

## Network policy inside the sidecar

All model and OAuth-refresh HTTP goes through one guarded `fetch`: exact origin
allowlist per mode (API key: `api.openai.com`; ChatGPT: plus
`auth.openai.com`; Codex: `chatgpt.com` + `auth.openai.com`; custom: the
validated origin), `redirect: "error"`, 16 MiB body cap, SDK retries disabled.
DNS for any other name fails. Codex uses SSE, never WebSockets.

## Credentials

- OAuth tokens and the custom token are Fernet-encrypted with the existing
  helper and `WW_CREDENTIALS_KEY` in `ai_credentials`, purpose-bound to their
  slot; the API key stays in its schema13 singleton with the step-up reveal.
- The sidecar's pi-ai `CredentialStore` is backed by RPC. Only the active
  mode's provider id resolves a secret. `modify` locks the slot on the backend,
  runs pi-ai's refresh in the sidecar, then commits; the backend validates the
  shape and persists the rotated token in its transaction **before**
  acknowledging, so a rotated refresh token is never lost. Failed refreshes
  release the lock without writing. The sidecar can never delete credentials;
  **Disconnect** is an admin action.
- Status responses expose only configured/updated/expiry metadata.

## Settings, activation and identity

`ai_settings` holds a non-secret draft and an explicitly activated copy.
Saving the draft never calls a model or changes extraction; **Use for
extraction…** activates after an AlertDialog confirmation. Settings: access
mode, model (pi-ai catalog for OpenAI/Codex, `/models` for custom), reasoning
effort (model metadata including Off, else standard levels), parallel streams
(1–8, default 1), postings per run (deferred postings are marked
`not_needed/stopped` and picked up next run), max turns, max tool calls, max
output tokens, request timeout and, for $0 modes, requests per run. API-key
mode keeps the environment USD stops (`WW_OPENAI_REQUEST_LIMIT`,
`WW_OPENAI_KNOWN_COST_STOP_USD`, `WW_OPENAI_DAILY_COST_STOP_USD`); each posting
can now use up to `max_turns` requests, so raise the request limit accordingly.

Jobs record `model_id=pi:<mode>:<model>`, `endpoint=<provider base URL>` and
`template_version=v9-pi-tools+<hash>`, where the hash covers mode, provider,
endpoint, model, reasoning effort, turn/tool/output caps and custom reasoning
metadata. The same identity is part of every input hash and cache key, so
results from different setups never collide. Historical Responses and Qwen rows
remain readable; schema15 rebuilds `inference_jobs` only to admit the `pi:`
identities, up to 8 streams and larger output caps, with row-equality
verification and dependency checks (rolls back otherwise). A job resumes only
under the identical active configuration.

## Commands

- Manual Refresh extracts with the active configuration after collection and
  normalization; progress reads "Extracting with <mode / model>".
- `uv run --project backend python -m waterlooworks_flash_next.inference
<database> start --posting-id <id>|--all` (plus `status|stop|resume`).
- `uv run --project backend python -m waterlooworks_flash_next.country_runner
<database> start --all|--posting-id <id> [--limit N]` and `stop <run-id>`.
  Selects postings without a current valid prediction; predictions remain
  separate from verified source facts.
- `npm run country -- status` and `npm run extract -- status|stop` still read
  historical runs. Their OpenAI-only `start`/`resume` paths were removed with
  the Responses bridge; the v7 source-span extraction CLI has no Pi port.

## Admin API (all admin-only, `Cache-Control: no-store`)

| Route                                                                              | Purpose                                                                    |
| ---------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `GET /api/admin/ai`                                                                | `{draft, active, activated_at, active_label, credentials{slot: metadata}}` |
| `PUT /api/admin/ai`                                                                | Save draft `AIConfig` (no model call)                                      |
| `POST /api/admin/ai/activate` / `deactivate`                                       | Body `{"confirm": true}`                                                   |
| `GET /api/admin/ai/models?mode=openai_api_key\|chatgpt_signin\|codex_device`       | Offline pi-ai catalog: `{models:[{id,name,context_window,efforts,…}]}`     |
| `POST /api/admin/ai/custom/test`                                                   | `{reachable,status_code,latency_ms,model_count,error_category}`            |
| `POST /api/admin/ai/custom/models`                                                 | `{models:[{id,name,loaded,context_window,supports_tools,efforts,…}]}`      |
| `PUT /api/admin/ai/custom/token`                                                   | `{token}`; never read back                                                 |
| `DELETE /api/admin/ai/credentials/{chatgpt_signin\|codex_device\|custom_token}`    | Disconnect                                                                 |
| `POST /api/admin/ai/chatgpt/start` / `complete`                                    | Authorize URL / `{callback_url}` paste                                     |
| `POST /api/admin/ai/codex/start`, `GET /api/admin/ai/login`, `POST …/login/cancel` | Device code, polling status, cancel                                        |

Every POST/PUT/DELETE requires the existing JSON/Origin/`X-WW-Request` guard
and an explicit JSON body (`{}` where no fields apply). Validation errors are
suppressed.

## Remaining live acceptance

Tests use local fakes only (a fake OpenAI-compatible server on 127.0.0.1, the
pi-ai faux provider, `httpx.MockTransport` auth endpoints). Real Sign in with
ChatGPT, Codex device login, OpenAI API calls, a real custom server, extraction
quality and the container seccomp profile with Node must be accepted live by
the owner before production use.
