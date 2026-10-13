# Discord new-job alerts

Every signed-in user (member or admin) can add Discord webhooks in **Settings › Discord alerts** (admins: **Admin settings › Discord alerts**). After each refresh that finds new jobs, every enabled webhook gets one message listing the top 5 new jobs by pay. A user can add up to 10 webhooks, in as many servers or channels as they like.

## Setting one up

1. In Discord, open **Server Settings → Integrations → Webhooks** (needs the Manage Webhooks permission).
2. **New Webhook**, pick the channel, **Copy Webhook URL**.
3. In Flash, give it a name, paste the URL, **Add webhook**, then **Test**.

Each webhook row shows its name, a hint (the last four digits of the webhook ID), the latest delivery result with relative time, an on/off switch, **Test** and **Delete** (confirmed in an AlertDialog). A typed but unsaved URL makes closing settings ask first.

## When alerts are sent

- New postings are those whose `postings.first_seen_run_id` is the run that just finished, so a posting is "new" exactly once, in the run that first saved it.
- The refresh service calls its finish listener after the run is finalized. Alerts are scheduled on a background thread from there, after the scheduler's own listener, so they never block, slow or fail the refresh.
- Completed and failed (partial) runs alert for the postings they saved. Interrupted runs (server shutdown) send nothing.
- A run that saved no new postings sends nothing. The very first collection into an empty database sends nothing either, since every posting would count as new.
- One message per enabled webhook per run, with up to 4 deliveries at a time. Content comes from the member-visible workspace snapshot (the same postings members see). The agent API cannot trigger alerts.

## Message format

The format is fixed (no per-user customisation yet). Structural example for a run with 12 new postings and 664 saved postings, with `WW_PUBLIC_BASE_URL=https://flash.example`:

```json
{
  "username": "WaterlooWorks Flash",
  "avatar_url": "https://flash.example/discord-avatar.png",
  "content": ":rotating_light: **New job alert**\n**12 new jobs** · top 5 by pay",
  "embeds": [
    {
      "color": null,
      "fields": [
        {
          "name": "Job title B",
          "value": "**Organization B**\n\n#️⃣ **ID:** [#100002](https://flash.example/?job=100002)\n📍 **Location:** City B · Hybrid\n💵 **Pay:** 26.63–33.26 CAD/hr\\*\n\n🗓️ 1 opening\n💼 1 application",
          "inline": true
        },
        {
          "name": "Job title A",
          "value": "**Organization A**\n\n#️⃣ **ID:** [#100001](https://flash.example/?job=100001)\n📍 **Location:** City A, Country A · Remote\n💵 **Pay:** 32 CAD/hr\n\n🗓️ 3 openings\n💼 32 applications",
          "inline": true
        },
        {
          "name": "Job title 12",
          "value": "**Organization 12**\n\n#️⃣ **ID:** [#100012](https://flash.example/?job=100012)\n📍 **Location:** Not listed\n💵 **Pay:** 27 CAD/hr",
          "inline": true
        },
        { "name": "Job title 11", "value": "…", "inline": true },
        { "name": "Job title 10", "value": "…", "inline": true },
        {
          "name": "​",
          "value": "🔗 [See all new jobs on flash.example](https://flash.example/)",
          "inline": false
        }
      ],
      "footer": { "text": "Showing 5 of 12 new jobs · 664 jobs on the site" }
    }
  ],
  "allowed_mentions": { "parse": [] }
}
```

- **Content:** `**1 new job**` for one posting, `**3 new jobs**, ranked by pay` for 2–5, `**12 new jobs** · top 5 by pay` above 5.
- **Footer:** `Showing 5 of 12 new jobs · 664 jobs on the site` when some are hidden, otherwise `3 new jobs · 664 jobs on the site`. It stays plain text because Discord does not render links in footers.
- **Link field:** one full-width field after the jobs with a link to the site. Discord field names cannot be empty, so its name is a zero-width space.
- **Ranking:** highest maximum CAD/hour first, matching the web app's salary tiers. An unspecified currency counts as CAD, other currencies use the Bank of Canada reference rate (fetched only when a new posting has a non-CAD rate), and weekly, monthly and yearly pay uses the stated weekly hours (else 40) and 52 weeks. Postings with no comparable maximum (no pay, needs review, open-ended minimum, daily pay, missing FX rate) come last. Ties go to the lower posting ID.
- **Location:** city, then the accepted inferred country name, then `·` and the work arrangement, each when known. `Not listed` when none is known.
- **Pay:** the web app's default hourly view in the original currency, rounded half-up to cents with grouped thousands: `32 CAD/hr`, `26.63–33.26 CAD/hr`, `25 CAD/hr (lower bound)`, `Needs pay review`, `Not listed`. `*` marks an unspecified currency shown as CAD. Differences from the web label: ranges use an en dash rather than `-`, mixed currencies or periods read `Mixed rates` instead of `--/--`, and the estimate notes from the tooltip are left out.
- **Counts:** `1 opening` / `3 openings`, `1 application` / `32 applications`. A line is left out when the count is unknown, and the blank line before them goes too when both are unknown. A missing organization line is left out, and a missing title reads `Untitled posting`.
- **Safety:** titles, organizations and locations have whitespace collapsed and every Discord markdown character backslash-escaped. Budgets of 200 characters for title and organization, 100 for city and 60 for country keep each field under Discord's limits (name 256, value 1024) and the embed under 6000 characters, and truncation never splits an escape. `allowed_mentions.parse = []` means source text can never ping anyone.
- **Without `WW_PUBLIC_BASE_URL`:** no `avatar_url`, no link field, and the ID shows as plain `#100002`.

**Test** sends a fixed embed titled `Test alert from WaterlooWorks Flash` with the same username, avatar and empty `allowed_mentions`.

## Delivery

- `httpx` with a 10 s timeout, `follow_redirects=False`, `trust_env=False`.
- 2xx: `ok`. 401 or 404: `gone`, and the webhook is switched off (the user can delete it and add a new one). 429: wait for `Retry-After` (header, else the JSON `retry_after`), capped at 10 s, then retry once. Anything else, including redirects, timeouts and network errors: `failed`.
- Each attempt records `last_status` plus `last_sent_at` (success) or `last_error_at` (failure).
- **Test** is limited to 3 per minute per user.

## Security

- Webhook URLs are bearer secrets: anyone holding one can post to the channel. Only `https://discord.com/api/webhooks/{numeric id}/{token}` is accepted, plus the `discordapp.com`, `ptb.discord.com` and `canary.discord.com` hosts, normalised to `discord.com`. Userinfo, ports, queries, fragments, trailing slashes and percent-encoding are rejected.
- URLs are encrypted at rest with the vetted Fernet helper and the backend `WW_CREDENTIALS_KEY`, and bound to the owning user (`discord-webhook-v1:{uid}`). There is no plaintext fallback. Without the key, adding returns 503, the list reports `available: false`, and delivery is skipped with a warning. Offline key initialization refuses a database that already holds webhook ciphertext.
- The URL is never returned by any endpoint, and never logged or echoed in errors. Validation errors use fixed text. Logs carry only the hint and a fixed status, and `httpx`/`httpcore` log records that contain a webhook path are dropped.
- The schema19 `discord_webhooks` table is excluded from the saved-data generation, so webhook changes never invalidate the shared workspace cache. Rows cascade with their app user. Users can only see and change their own webhooks.

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
