# Discord new-job alerts

Every signed-in user (member or admin) can add Discord webhooks in **Settings › Discord alerts** (admins: **Admin settings › Discord alerts**). After each refresh, every enabled webhook gets one message about new jobs that are ready to show (details collected and AI extraction finished), listing the top 5 by pay. Each job is announced to each webhook once. A user can add up to 10 webhooks, in as many servers or channels as they like.

## Setting one up

1. In Discord, open **Server Settings → Integrations → Webhooks** (needs the Manage Webhooks permission).
2. **New Webhook**, pick the channel, **Copy Webhook URL**.
3. In Flash, give it a name, paste the URL, **Add webhook**, then **Test**.

Each webhook row shows its name, a hint (the last four digits of the webhook ID), the latest delivery result with relative time, an on/off switch, **Test** and **Delete** (confirmed in an AlertDialog). A typed but unsaved URL makes closing settings ask first.

## When alerts are sent

A refresh caps detail requests (`WW_DETAIL_LIMIT`) and AI extraction (the AI setting **postings per run**), so a posting can be saved from the listing several runs before its details and pay are known. Alerts therefore wait until a posting is **ready**:

- its detail content is saved by the current extractor (`posting_contents.extractor_version` is the current `EXTRACTOR_VERSION`), and
- when an AI configuration is activated, an extraction of exactly that saved content (`inference_job_items.selected_content_hash` equals the current `posting_contents.content_hash`) finished as `accepted`, `rejected`, `failed`, or `not_needed` without an error category (the deterministic parser already knew the pay and skills), or a current model projection exists. Items deferred by the per-run cap (`not_needed` + `stopped`), `interrupted`, `pending` and `running` items are not ready.
- Without an activated AI configuration, saved detail content alone is ready.

The rule lives in `alert_readiness.ready_posting_ids`.

After every completed or failed (partial) run, each enabled webhook is considered on its own. Its **candidates** are postings that are:

1. listed in the run that just finished (still on the board),
2. first seen at or after the webhook was added, so a new webhook never receives the existing backlog,
3. not from the first collection into an empty database (the earliest run that saved postings is a baseline; nothing in it is ever alerted),
4. ready, present in the member workspace snapshot, and not yet announced to this webhook,
5. first seen within the last 7 days.

Unannounced postings first seen more than 7 days ago are marked announced without being sent, so they never appear later. With no candidates the webhook gets nothing. Otherwise it gets one message, and all its candidates (listed and summarised) are marked announced only after Discord accepts it (2xx). After a failure they stay unannounced and are retried after the next run; `gone` webhooks are switched off, so they are not retried. Announcements are kept per webhook in the schema20 `discord_webhook_announced` table.

- The refresh service calls its finish listener after the run is finalized. Alerts are scheduled on a background thread from there, after the scheduler's own listener, so they never block, slow or fail the refresh. Interrupted runs (server shutdown) send nothing.
- Up to 4 deliveries at a time. Content comes from the member-visible workspace snapshot (the same postings members see). The agent API cannot trigger alerts.

### Getting new jobs ready sooner

- **Details:** after the posting the browser bootstrap needs on the board's first page, postings without any saved detail content come before re-fetches of saved ones, and among them the newest first sighting goes first. A request cap therefore delays the old backlog, not new jobs.
- **AI extraction:** postings whose current content was never analysed come first, newest first sighting first, before retries of earlier rejected or failed outcomes, so the per-run cap is spent on new jobs.

## Message format

The format is fixed (no per-user customisation yet): one embed per listed job, at most 5. Structural example for a webhook with 12 ready new jobs, 3 more still being analysed and 664 saved postings, with `WW_PUBLIC_BASE_URL=https://flash.example`:

```json
{
  "username": "WaterlooWorks Flash",
  "avatar_url": "https://flash.example/discord-avatar.png",
  "content": ":rotating_light: **New job alert**\n**12 new jobs** · top 5 by pay",
  "embeds": [
    {
      "title": "Job title B",
      "url": "https://flash.example/?job=100002",
      "color": 14066687,
      "description": "**Organization B**\n📍 City B · Hybrid\n💵 26.63–53.26 CAD/hr\\*\n🗓️ 1 opening · 💼 1 application",
      "footer": { "text": "#100002" }
    },
    {
      "title": "Job title A",
      "url": "https://flash.example/?job=100001",
      "color": 9090303,
      "description": "**Organization A**\n📍 City A, Country A · Remote\n💵 32 CAD/hr\n🗓️ 3 openings · 💼 32 applications",
      "footer": { "text": "#100001" }
    },
    { "title": "Job title 9", "…": "…" },
    { "title": "Job title 8", "…": "…" },
    {
      "title": "Job title 7",
      "url": "https://flash.example/?job=100010",
      "color": 7724961,
      "description": "**Organization 7**\n💵 27 CAD/hr\n💼 4 applications\n🔗 [See all new jobs on flash.example](https://flash.example/)",
      "footer": {
        "text": "#100010 · Showing 5 of 12 new jobs · 664 jobs on the site · 3 still being analysed"
      }
    }
  ],
  "allowed_mentions": { "parse": [] }
}
```

- **Content:** `**1 new job**` for one posting, `**3 new jobs**, ranked by pay` for 2–5, `**12 new jobs** · top 5 by pay` above 5. The count is this webhook's candidates.
- **Title and link:** the job title (`Untitled posting` when missing) links to `<base>/?job=<id>`.
- **Color:** the web app's salary tier for the maximum CAD/hour, using the dark-theme tier colors: green `#75DFA1` (20+), blue `#8AB4FF` (30+), gold `#FFD166` (40+), purple `#D6A3FF` (50+), red `#FF5252` (65+, also 80+). `null` below 20 or without comparable pay.
- **Description:** the organization in bold, `📍` location, `💵` pay, then `🗓️` openings and `💼` applications on one line, each left out when unknown (pay always shows, as `Not listed` when unknown). The last embed ends with the site link.
- **Footer:** `#<id>` on each embed. The last one adds `Showing 5 of 12 new jobs · 664 jobs on the site` when some are hidden, otherwise `3 new jobs · 664 jobs on the site`, then `· K still being analysed` when postings first seen since the webhook was added (within 7 days, still listed) are not ready yet. Footers stay plain text because Discord does not render links there.
- **Ranking:** highest maximum CAD/hour first, matching the web app's salary tiers. An unspecified currency counts as CAD, other currencies use the Bank of Canada reference rate (fetched only when a listed posting has a non-CAD rate), and weekly, monthly and yearly pay uses the stated weekly hours (else 40) and 52 weeks. Postings with no comparable maximum (no pay, needs review, open-ended minimum, daily pay, missing FX rate) come last. Ties go to the lower posting ID.
- **Location:** city, then the accepted inferred country name, then `·` and the work arrangement, each when known. The line is left out when none is known.
- **Pay:** the web app's default hourly view in the original currency, rounded half-up to cents with grouped thousands: `32 CAD/hr`, `26.63–33.26 CAD/hr`, `25 CAD/hr (lower bound)`, `Needs pay review`, `Not listed`. `*` marks an unspecified currency shown as CAD. Differences from the web label: ranges use an en dash rather than `-`, mixed currencies or periods read `Mixed rates` instead of `--/--`, and the estimate notes from the tooltip are left out.
- **Counts:** `1 opening` / `3 openings`, `1 application` / `32 applications`.
- **Safety:** titles, organizations and locations have whitespace collapsed and every Discord markdown character backslash-escaped. The title is capped at Discord's 256 characters, and budgets of 200 characters for the organization, 100 for city, 60 for country and 120 for pay keep five embeds under Discord's 6000-character total (description 4096, footer 2048, at most 10 embeds); truncation never splits an escape. `allowed_mentions.parse = []` means source text can never ping anyone.
- **Without `WW_PUBLIC_BASE_URL`:** no `avatar_url`, no title links and no site link.

**Test** sends a fixed embed titled `Test alert from WaterlooWorks Flash` with the same username, avatar and empty `allowed_mentions`.

## Delivery

- `httpx` with a 10 s timeout, `follow_redirects=False`, `trust_env=False`.
- 2xx: `ok`. 401 or 404: `gone`, and the webhook is switched off (the user can delete it and add a new one). 429: wait for `Retry-After` (header, else the JSON `retry_after`), capped at 10 s, then retry once. Anything else, including redirects, timeouts and network errors: `failed`.
- Each attempt records `last_status` plus `last_sent_at` (success) or `last_error_at` (failure).
- **Test** is limited to 3 per minute per user.

## Security

- Webhook URLs are bearer secrets: anyone holding one can post to the channel. Only `https://discord.com/api/webhooks/{numeric id}/{token}` is accepted, plus the `discordapp.com`, `ptb.discord.com` and `canary.discord.com` hosts, normalised to `discord.com`. Userinfo, ports, queries, fragments, trailing slashes and percent-encoding are rejected.
- URLs are encrypted at rest with the vetted Fernet helper and the backend `WW_CREDENTIALS_KEY`, and bound to the owning user (`discord-webhook-v1:{uid}`). There is no plaintext fallback. Without the key, adding returns 503, the list reports `available: false`, and delivery is skipped with a warning. Offline key initialization refuses a database that already holds webhook ciphertext.
- The URL is never returned by any endpoint, and never logged or echoed in errors. Validation errors use fixed text. Logs carry only the hint, a fixed status and a job count, and `httpx`/`httpcore` log records that contain a webhook path are dropped.
- The schema19 `discord_webhooks` and schema20 `discord_webhook_announced` tables are excluded from the saved-data generation, so webhook changes and announcements never invalidate the shared workspace cache. Webhook rows cascade with their app user, and announcement rows with their webhook. Users can only see and change their own webhooks.

## API

Session-cookie routes for every signed-in user, with the usual Origin, JSON and `X-WW-Request` guards on mutations. Every response is `no-store`.

| Method | Path                                      | Purpose                                                                 |
| ------ | ----------------------------------------- | ----------------------------------------------------------------------- |
| GET    | `/api/account/discord-webhooks`           | `{webhooks, limit, available}`, never URLs                              |
| POST   | `/api/account/discord-webhooks`           | `{label, url}` → 201 webhook; 409 duplicate/limit, 422 invalid, 503 key |
| PATCH  | `/api/account/discord-webhooks/{id}`      | `{label?, enabled?}`                                                    |
| DELETE | `/api/account/discord-webhooks/{id}`      | remaining list                                                          |
| POST   | `/api/account/discord-webhooks/{id}/test` | `{}` → webhook with the test result; 429 after 3 per minute             |

## Configuration

- `WW_PUBLIC_BASE_URL`: the site's public origin (HTTPS, no path; plain HTTP only for `localhost`/`127.0.0.1`). It sets the job links, the site link and the avatar (`<base>/discord-avatar.png`, served from `frontend/public`). It is read from the backend process environment at startup, and an invalid value fails startup with a fixed message. Production Compose sets it to `https://${WW_PUBLIC_HOST}`. When unset, links and the avatar are left out rather than guessed.
- `WW_CREDENTIALS_KEY` / `WW_CREDENTIALS_KEY_FILE`: the existing backend key; nothing new to provision.
