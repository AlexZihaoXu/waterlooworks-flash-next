# Deterministic saved-field parsers

The parser entry points remain **pure library functions**, not database migrations, API endpoints, UI features, model jobs or collection actions. Schema v8 can persist their outputs only through explicit offline analysis commands; current public API/UI contracts remain unchanged. Outputs carry `structured-fields-v1`, recognized/unknown/needs-analysis state, source field/raw/presence and optional caller-supplied canonical source hash. Evidence and spans may contain private source text; keep them local and never log full results.

## Entry points

- `structured_fields.parse_fields(fields, listing, content_hash=..., listing_hash=...)`: arrangement, separate city/province/country, multiple levels, work term, duration, job-type source value, targeted disciplines, counts and deadline.
- `skill_parser.parse_skills(fields, source_hash=...)`: bounded explicit vocabulary matches/exclusions with exact field spans and explicit requirement evidence.
- `compensation_parser.parse_compensation(value, source_hash=..., present=...)`: conservative base-pay fastpath with Decimal bounds, optional currency/period/hours and independent payout frequency.
- `compensation_parser.validate_pay_evidence(candidate, source, evidence)`: rejects unsupported model candidates; no inference or extraction request is issued.
- `application_parser.parse_application_instructions(fields, source_hash=...)`: external-application tri-state, route and classified safe source URLs, independent of source Application Method.

Callers supply saved label/value mappings and listing primitives, not reconstructed assumptions about postings. Missing fields and explicit nulls retain distinct `SourceEvidence.present`/`raw` values. Projection failure never becomes zero or an invented enum. No publication time, geographic equivalence, salary currency, annual period, working hours, proficiency or source availability is inferred.

## Labeled fields and listing primitives

Arrangement recognizes exactly Remote/Hybrid/In-person after trimming. Geographic city/province/country remain separate explicit source strings; punctuation-only and unknown placeholders stay unknown. Country is not inferred from city or currency. The existing public `PostingResponse.location` is not renamed or repurposed.

Level splits newline-delimited Junior/Intermediate/Senior, preserving multiple known levels and separately retaining unknown future labels. Work term recognizes a four-digit year and explicit Winter/Spring/Summer/Fall season. Duration keeps count and month/work-term unit, plus only explicit required/preferred wording; optional `work term`, `consecutive work term`, and `commitment` source suffixes are recognized. Two work terms are never converted into eight months. Job type remains a source string. Targeted disciplines reuse the existing exact-source splitter, without inferring skills.

Counts accept finite nonnegative integral JSON numbers within `2**53 - 1`, plain digit strings or correctly comma-grouped digit strings. Booleans, negative/nonfinite/fractional numbers, signs, decimal strings and malformed grouping are rejected. Source type remains evidence. Listing application counts/openings/deadlines take precedence when explicitly present; absent listing values can use corresponding saved labels, but invalid or null present values do not silently fall back.

Backend deadline parsing mirrors the existing frontend abbreviated-month/12-hour grammar in `America/Toronto`. It returns UTC-aware datetimes and rejects invalid calendars, unknown formats, DST gaps and repeated times. `tzdata` supplies IANA data on Windows; the frontend implementation is untouched.

## Skills

The small explicit vocabulary is defined in one module. It includes C++, C#, Java, JavaScript, TypeScript, Python, SQL, Kubernetes, Docker, Git, Linux, Node.js, .NET, HTML, CSS, CI/CD (including CI-CD), and object-oriented design. Word/token boundaries prevent Java from matching JavaScript or JavaBeans. Only Required Skills, Job Responsibilities and Job Summary are scanned; disciplines and titles are not used to infer skills.

Negated/optional clauses are excluded conservatively with retained span evidence. Required/preferred is assigned only when an explicit supported qualifier appears in the same clause without a conflicting qualifier; the field name alone does not imply required. Qualifier spans are returned separately. Unspecified means no supported relationship, not optional. Clause-wide exclusion can miss non-negated terms in mixed clauses; ambiguous semantics and vocabulary expansion remain later work. Original scanned fields remain available, so unknown phrases are not silently declared absent or irrelevant. This is not an exhaustive skill inventory or a proficiency assessment.

## Compensation

The entire trimmed field must fit a reviewed simple amount/range/open-bound grammar, optionally with explicit pay prefix, currency, period, fixed weekly hours and payout frequency. Amounts use Decimal and expand `k` without floating-point conversion. Fixed bounds are equal; ranges remain ranges; minimum/maximum bounds retain inclusive versus exclusive semantics.

Dollar signs alone never establish currency. Only supported explicit pay-related ISO codes or unambiguous currency names in the matched expression qualify. Missing period stays unknown regardless of amount size. Monthly base pay paid biweekly remains monthly, with biweekly payout frequency separate. No hours, exchange rates or annual equivalents are calculated.

Benefits, allowances, tiered eligibility, multiple equivalents, mixed currencies, reversed ranges, malformed numbers and unsupported prose return `needs_analysis`, not a collapsed or guessed base-pay row. Fastpath grammar is limited to 1,024 characters; saved fields remain bounded by the existing 200,000-character source contract. Missing/blank/generic no-amount text returns unknown without suggesting automatic model work.

The reusable evidence validator requires an exact source span and a candidate supported by the whole-field deterministic parse. It prevents quoting a housing stipend as base pay or inventing currency/annual period. Complex future model tiers will require a separately reviewed validator extension; this function intentionally does not approve them yet.

## Application instructions

Application Method is retained separately: WaterlooWorks can still require an employer application. Recognized dual-route/must-also/mandatory-external instructions set the external-required flag true. Explicit optional/not-required wording can set it false. Missing, conditional or unrecognized wording stays unknown; conflicting recognized instructions require analysis. Source method alone never sets external-required false.

HTTP(S) candidate links retain query strings, field/span and supporting instruction text, classified as required, optional, not-required, uncertain or informational. Bare/unassociated URLs remain uncertain; explicit company/info-session references are informational. Non-HTTP(S), userinfo-bearing, malformed-port/control/backslash URLs are not returned as clickable candidates. No URLs are visited, resolved, authenticated or applied through. Original source text remains evidence, including unsupported instructions.

## Normalized storage seam and verification

Schema v7 stores immutable projections keyed to exact listing/content revision IDs and hashes, parser version, and separate producer provenance. Fixed values and parser states are queryable; child tables retain levels, disciplines, accepted/excluded skill mentions, multiple compensation rates, and application instruction/link spans. Arrangement remains separate from city/province/country. Decimal bounds and hours remain exact text, and nullable currency/period are not filled in.

The deterministic current pointer accepts output only when both source revisions still match the saved current posting. Model-provenance roots can contribute validated rate/skill children but cannot write fixed/application rows or replace the deterministic pointer. Raw field values, scanned descriptions, and whole posting bodies are not copied into parser-result JSON; readers reconstruct field/span evidence from compact immutable source history.

`normalized_storage.normalize_saved_posting` owns one per-posting transaction and is idempotent for the same revisions/producer/parser version. `normalize_saved_postings` is resumable between posting commits. `python -m waterlooworks_flash_next.normalized_storage <database>` is the intentional local CLI. Migration creates tables only; imports, startup, GET requests, refresh and ordinary reads do not analyze saved rows. These functions never collect, authenticate, call a network/model, convert pay units/currencies, or modify source history.

Schema v8 adds explicit durable local inference jobs. `python -m waterlooworks_flash_next.inference <database> start --posting-id <id>` runs at most 20 selected saved postings; `status`, `stop`, and `resume` never run implicitly. The fixed raw Qwen template uses the official explicit low-effort sentence with thinking on, maximum 4,096 completion tokens, loopback-only LM Studio, no redirects/proxies, at most four concurrent calls and bounded retries. Expired work is visibly interrupted and requires explicit resume.

Model input is canonical JSON of every saved content field, extraction flags and existing deterministic skills. Listing observations are excluded. Cache provenance includes the content/input hashes and exact model/template/input/validator configuration, preventing application-count-only refreshes from repeating inference. Raw prompts, responses and reasoning are not retained.

Strict final JSON is accepted only after the thought boundary closes and every rate/skill is validated against a unique exact source substring. Complex fixed/range/open pay and distinct eligibility tiers are supported. Currency, period, hours and payout cannot be invented; unknown currency/period are valid. Benefits/stipends/equivalents and negated/optional or unqualified skill claims fail closed. Accepted rows are per-posting atomic model projections; changed content prevents acceptance and hides stale pointers. Deterministic fixed/application output remains authoritative. Public API/UI, comparison/FX and inference scheduling beyond explicit commands remain deferred.

Tracked tests use only generic field/value/text/URL primitives and empty databases. Private-copy acceptance migrates saved history, processes all authorized rows offline, checks aggregate coverage, source evidence, idempotency, stale-current and atomic rollback guards, and reports no posting bodies or URLs. Coverage is not a precision claim; conservative unknown/needs-analysis results are intentional.
