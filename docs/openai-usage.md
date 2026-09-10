# OpenAI usage accounting

The accounting helpers in `backend/src/waterlooworks_flash_next/openai_usage.py` are connected to all actual Python and normal Node provider paths through the shared persisted attempt ledger. Admin settings displays the most recent20 runs via the admin-only no-store usage GET. Saving a key, reading status and startup never make paid requests. See [provider limits and pending live gates](openai-provider.md).

## Rates and counts

Fixed estimate: `gpt-5.6-luna`, Standard/default service tier, global endpoint, pricing dated2026-09-09. USD per million tokens:

| Category                    | Normal context | More than272,000 input tokens |
| --------------------------- | -------------: | ----------------------------: |
| Ordinary input              |           0.20 |                          0.40 |
| Cached input reads          |           0.02 |                          0.04 |
| Cache writes                |           0.25 |                          0.50 |
| Output, including reasoning |           1.20 |                          1.80 |

Sources: [model](https://developers.openai.com/api/docs/models/gpt-5.6-luna), [pricing](https://developers.openai.com/api/docs/pricing), [cache usage fields](https://developers.openai.com/api/docs/guides/prompt-caching). Longer-context rates apply to the whole request, not only tokens above the threshold. These are estimates, excluding taxes or regional processing surcharges; not an OpenAI invoice or Batch/Flex pricing.

Use provider-reported counters, not text length or a local tokenizer:

- `input_tokens` includes ordinary input, cached reads and cache writes.
- Read `cached_tokens` and `cache_write_tokens` from `usage.input_tokens_details`.
- Ordinary input = input − cached reads − cache writes.
- `output_tokens` already includes reasoning tokens. Do not add `output_tokens_details.reasoning_tokens` again.
- Total tokens = input + output. Cache and reasoning subtotals are not additional tokens.
- Price each category separately and divide the weighted sum by1,000,000. Decimal arithmetic retains sub-cent charges; round only for display after aggregation.
- Missing required counters make cost unknown, not zero. Explicit all-zero counters can cost zero. Unknown models or service tiers are not priced using Luna's Standard rates.
- Reject negative, Boolean, fractional or inconsistent counters.

## Provider integration

The shared adapter persists one record per actual API attempt, including rejected/incomplete output. Finalization is idempotent and precedes evidence validation. This client is nonstreaming and does not retry ambiguous paid requests. It retains model, actual service tier, pricing version, attempt identity and available usage, never prompts, credentials or raw provider diagnostics.

A timeout or lost response may still incur provider charges. Record it as unpriced when usage is unavailable. Aggregate known cost separately and label totals incomplete while any attempt remains unpriced. Do not apply long-context rates to an aggregate token count: price each request first, then sum. Retries are separate attempts, not replacements for the first charge.
