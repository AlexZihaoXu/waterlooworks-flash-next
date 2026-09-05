# Contributing

## Workflow

Use test-driven development: write a failing test, make the smallest passing change, then refactor. Initial tests cover only package import and the placeholder UI; no WaterlooWorks behavior is assumed or tested yet.

Use Conventional Commits: `type(scope): description`. Scopes are optional; mark breaking changes with `!` or a `BREAKING CHANGE:` footer. Commit enforcement is currently a review responsibility.

Before committing, run the checks documented in the README. CI runs the same quality gates; repository branch protection must be configured separately by the owner to require them before merging.

## Privacy and platform exploration

- Keep `.experiment/` in `.gitignore` at all times.
- Use `.experiment/test/` for guided interactive uv Python sessions.
- Do not add mock postings, fabricated responses, or speculative platform behavior.
- Never commit credentials, cookies, session state, private screenshots, or fetched postings.
- Do not introduce live WaterlooWorks access into default tests or CI. Any future live tests require an explicit opt-in mechanism before they are added.
- Update `docs/scraping-workflow.md` with verified Playwright navigation, actions, and retrieval points as they are discovered.
