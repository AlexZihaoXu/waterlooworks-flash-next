# Inferred work country (schema 10)

Country inference is an **explicit saved-data-only local CLI**, not a refresh or automatic API action. It never reruns or replaces salary/keyword extraction, normalized projections or source facts. The existing listing `country: null` and deterministic detail country values retain their semantics. New `country_inference` metadata appears on workspace postings and saved detail responses.

## Contract and display

- Predict the country **where the job is worked**, not employer headquarters, currency, nationality, citizenship or application eligibility. Geographic names may be mapped to countries by the model.
- Only recognized ISO 3166-1 alpha-2 codes (249 entries from MIT-licensed `flag-icons`) are accepted. There is no default country. Model-reported confidence must be finite and at least 0.85; it is **not a calibrated probability of correctness**.
- States: `not_requested`, `pending`, `accepted`, `unknown`, `failed`, `stale`. Only accepted, source-current predictions have a code/name/flag. Missing or ambiguous results remain unknown, not a fallback to an earlier success.
- The latest intended run **per posting** selects the result. Thus a pilot does not hide unrelated postings' results; a newer pending/failed/unknown selection never exposes an older accepted flag for that posting. Cached accepted/unknown values can originate in an older run only with identical configuration and both input hashes; `origin_run_id` records this.
- Configuration carries model identity/configuration, prompt hash/version, code hashes, reasoning, temperature, completion limit, request timeout and concurrency. Resume requires exactly the original configuration. The reader validates the configuration digest, supported version/prompt and tested model profile.
- Both listing and optional content hashes plus immutable revision references are checked. A change to **either** input hides the prediction, including content appearing where it was previously absent. Hash identity permits source reversions, not guessed historical data. Listing count changes conservatively invalidate country predictions too.
- The reader uses one bulk query in a consistent snapshot, not a per-card query. Country commits invalidate the existing workspace byte cache through SQLite data-version observation. Reload to see CLI changes; no inference is triggered by GET, startup or SSE.
- Location fields in cards, At a glance and Similar jobs show the existing location pin, a country flag when available, then the location text. There is no separate country row or visible “inferred” caption. The filter is labelled **Country**, with name-based URL values; broad search additionally matches accepted country names/codes. Unknown/stale/failed values have no flag or country filter option. The API still preserves the distinct inference provenance and original source geography.
- Owner-approved native `<img>` elements use `flag-icons` rectangular (4:3) SVG assets at the field-icon width of16px, preserving the artwork's intrinsic ratio with auto height and subtle2px corners. The Country filter modal uses the same native flag styling beside its checkbox labels. Country names remain accessible through image alt text in location fields and existing checkbox labels in the picker. HeroUI Avatar's documented props cannot provide this small rectangular shape, so the narrowly approved native-image exception replaces it; no custom React component, emoji, remote assets, library CSS or HeroUI style overrides are used. MIT attribution is retained in the dependency, backend reference-data license and public `flag-icons.LICENSE.txt`.
- Country never determines compensation currency. Assumed-currency stars, source-backed pay, max-pay styles, daily-pay sort handling and hidden Similar scores are unchanged.

## Input and evidence safeguards

Input contains the complete saved listing city, reviewed job location/address/arrangement fields, and complete Job Summary/Responsibilities. Salary, qualifications and employer identity fields are deliberately excluded. Full relevant input is capped at 24,000 UTF-8 bytes; oversized input becomes terminal `unknown/input_too_large` without generation, **never silent truncation**. Missing location context is also terminal unknown. Text is JSON-encoded with role-delimiter angle brackets escaped and explicitly treated as untrusted data, not instructions.

The model must consider all supplied location statements, returning unknown for ambiguous cities, unrestricted worldwide remote work, multiple possible countries and low confidence. Narrow deterministic guards reject sole bare London evidence and standalone unrestricted work-location clauses such as “remote worldwide” or “anywhere”; “anywhere in Canada” remains scoped. Generic prose about worldwide customers is not treated as a work-location restriction. These guards are deliberately not a geographic database; broader ambiguity and model mistakes require private pilot review.

Accepted predictions require 1–3 exact, uniquely resolving quotes (maximum 500 characters each) from supplied fields. Generated offsets are not accepted. The validator resolves Unicode-code-point spans and stores source field, span and quote hash, not copied prompts/responses/reasoning. The API reconstructs bounded location context and rechecks its hash. Obvious headquarters, citizenship, nationality and compensation quote anchors are rejected. **Location evidence is context for a prediction, not proof of its geographic correctness.** Source grounding does not make model guesses verified facts.

## Deployment and explicit commands

Only the owner should deploy: ensure refresh/extraction are idle, take a consistent SQLite backup, migrate, restart the API safely, and inspect a small pilot before the full workspace. Never run this concurrently with another model workload. Worker implementation validation does not authorize production inference.

From the repository root, after the owner's consistent backup:

```sh
# Explicit schema-only migration; no normalization, salary/keywords or inference.
uv run --project backend --locked python -c "from pathlib import Path; from waterlooworks_flash_next.database import open_database; open_database(Path('data/waterlooworks.sqlite3')).close()"

# Small deterministic saved-ID-order pilot, or repeat --posting-id for reviewed IDs.
npm run country -- start --all --limit 8 --concurrency 4 --hours 0.1
npm run country -- status
npm run country -- status <RUN_ID>
npm run country -- stop <RUN_ID>
npm run country -- resume <RUN_ID> --concurrency 4 --hours 10

# Only after pilot review; reuses matching accepted/unknown pilot checkpoints.
npm run country -- start --all --concurrency 4 --hours 10
```

All commands accept `--database PATH`; the default is `data/waterlooworks.sqlite3`. The CLI requires an existing schema10 database and never migrates it. Status opens read-only and does not discover models. Stop only sets a durable flag. Start/resume require the already-loaded `unsloth/gemma-4-26b-a4b-it`, Q2_K_XL, context65536, parallel4 and supported reasoning off (`none`). The runner verifies model identity before and after each completion. No model load, configuration change, cloud inference, credentials, login or collection occurs.

Concurrency is bounded 1–4, the run deadline is at most24 hours, and each completion has a120-second hard deadline with no retries. The run deadline stops new work; in-flight bounded requests may finish. Stop/SIGINT/SIGTERM likewise stop claims without claiming provider cancellation. Heartbeats renew an owned, unexpired120-second lease every5 seconds. A live lease prevents a second country owner. Expired running leases require explicit resume: lost in-flight items become terminal `failed/interrupted`, never silently reissued. Resume processes pending items only; accepted/unknown/failed/stale remain terminal. A new explicit start may retry failed/stale work while reusing matching accepted/unknown results. A completed run cannot be resumed. Exit0 means the queue is exhausted (inspect counts for unknown/failed); exit1 is a fatal infrastructure/setup failure; exit2 means stopped/pending work remains. Errors are fixed sanitized categories, never source/provider exception text.

## Acceptance boundaries

Tracked tests use primitives and empty SQLite only. Private migration/contract exercises use a read-only production backup under `.experiment/country-verification/`: all34 pre-existing tables and schema objects were unchanged during migration of1,218 saved postings, with integrity/FKs passing. Private checkpoint exercises derive test checkpoints from existing exact country source fields; they make **zero model calls** and do not establish inference accuracy.

Production deployment is now verified: a consistent schema9 backup preceded the additive schema10 migration; all33 pre-existing application tables and their rows/schema objects were preserved (the internal SQLite sequence table is excluded from that count). Integrity and foreign-key checks passed. The country-only run processed1,218 saved postings in43m6s:1,212 accepted predictions across seven countries and six terminal `invalid_output` failures. Failed results remain visibly unknown with no flag; no automatic retries or fallback countries were applied. All prior salary/keyword/source/history rows remained unchanged after inference.

A source-country comparison, normalizing US and Türkiye naming aliases, agreed on1,204 accepted results. The eight differences were reviewed: seven explicitly Canada-restricted remote jobs whose structured source country was US, and one Taiwan location with a placeholder country field. This is a consistency check, not an exhaustive accuracy or calibration claim. Country confidence remains model-reported.

Live browser checks passed all seven inferred-country filters, country-plus-ID search, local SVG loading, failed-result no-flag behavior, and desktop/mobile Light/Dark layouts without flag/name overlap or external browser requests. Backend/frontend quality gates and generated API contracts passed. Private audit and visual evidence are under `.experiment/country-pilot/`; do not commit saved postings or model outputs. Broader model ambiguity, prompt-injection robustness and calibrated accuracy remain limitations; source-grounded evidence does not make these predictions verified facts.
