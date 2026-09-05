# Contributing

## Workflow

Use test-driven development: write a failing test, make the smallest passing change, then refactor. Initial tests cover only package import and the placeholder UI; no WaterlooWorks behavior is assumed or tested yet.

Use Conventional Commits: `type(scope): description`. Scopes are optional; mark breaking changes with `!` or a `BREAKING CHANGE:` footer. Commit enforcement is currently a review responsibility.

Before committing, run the checks documented in the README. CI runs the same quality gates; repository branch protection must be configured separately by the owner to require them before merging.

## UI components

Follow the [HeroUI UI component rules in AGENTS.md](AGENTS.md#ui-component-rules):

- Check the official HeroUI v3 component list, reference designs/examples, and component-specific usage documentation before using a component.
- Do not make CSS-level modifications to HeroUI components. Use documented props and variants, not custom CSS, inline styles, or class overrides.
- Do not invent custom UI components without explicit user permission. First confirm that no HeroUI component or documented composition provides a close fit, explain the gap, and ask for approval.
- Explicitly highlight any approved custom component in the handoff, including the reason and affected files.

## Privacy and platform exploration

- Keep `.experiment/` in `.gitignore` at all times.
- Use `.experiment/test/` for guided interactive uv Python sessions.
- Do not add mock postings, fabricated responses, or speculative platform behavior.
- Never commit credentials, cookies, session state, private screenshots, or fetched postings.
- Do not introduce live WaterlooWorks access into default tests or CI. Any future live tests require an explicit opt-in mechanism before they are added.
- Update `docs/scraping-workflow.md` with verified Playwright navigation, actions, and retrieval points as they are discovered.
