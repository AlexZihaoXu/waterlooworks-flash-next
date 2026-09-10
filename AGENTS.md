# Project instructions

Follow `README.md` and `CONTRIBUTING.md`, including TDD, Conventional Commits, and the no-mock-data policy. Keep `.experiment/` ignored and private data out of Git.

## Credentials

The root `.credentials.json` is obsolete: runtime code must never load it or migrate its contents. Keep its Git, Docker and formatting exclusions as protective entries. Agents must never view, read through tools, search, print, or otherwise expose its contents; exclude it from searches and diagnostic collection. The owner may delete it unread after review; never overwrite it.

WaterlooWorks username/password are entered by the admin directly through the account button's Admin settings modal. Only backend credential/authentication code may handle the saved pair at runtime. Store only the authenticated-encrypted pair in SQLite; decrypt one coherent pair for an explicit login. The owner narrowly authorizes admin-only GET to decrypt internally and return the saved username/email plus safe metadata, never the password. Password drafts start empty; a fixed placeholder mask is not replacement input. The independent backend `WW_CREDENTIALS_KEY` belongs in ignored `.env` or a runtime secret, never the database, frontend or an image layer. Initialization is an explicit offline operator command, never startup/tests on real data. Never silently rotate keys or fall back to plaintext.

OpenAI API keys use the same vetted Fernet helper and independent backend key, in a separate schema13 singleton. Plaintext readback is authorized only in the dedicated no-store reveal POST after a one-use, short-lived challenge and another server-verified Google OAuth authentication for the same current app session and admin UID. Old tokens, a fresh `iat`, token refresh alone and recent login are not step-up. The UI clears revealed keys after30seconds, Hide, close, logout, account change or tab hiding. This is not permission for agents, other endpoints, logs or browser persistence to expose secrets. Saving configuration never invokes OpenAI or switches extraction.

Agents must not inspect `.env`, private databases, session state, or sensitive form values. Never hardcode, copy, log, return, or include secret values in tool output, exceptions, screenshots, traces, or other artifacts. Validation errors must suppress input values, including malformed JSON and extra fields. Do not change user-provided credentials. Future containers require explicit runtime secrets/read-only mounts for keys and a private runtime database volume, never build-time secrets.

## Pi plugins

Do not install Pi plugins or add them to project settings for automatic installation. If a plugin is needed, explain why and ask the user to install it themselves. This applies to both global and project-local installations.

## HeroUI v3

The official HeroUI React MCP server is configured project-locally in `.mcp.json`. The MCP adapter must be supplied by the user's Pi setup; this repository must not install it. `.pi/mcp.json` enables startup connection, keep-alive, and direct tools for `heroui-react` when the adapter is available.

Before implementing or changing HeroUI UI, consult the official MCP tools for the relevant v3 documentation. Do not assume v2 APIs. If the server is unavailable, report the connection problem rather than claiming its documentation was checked. Keep this configuration project-scoped; do not move it into global settings.

### UI component rules

- Use HeroUI components rather than inventing custom UI components. Never create a custom UI component without explicit user permission.
- Before using a specific component, check HeroUI's official v3 component list, reference designs/examples, and that component's usage documentation. Use the official MCP tools; do not rely on memory or v2 APIs.
- Never make CSS-level modifications to HeroUI components: no custom CSS overrides, inline styles, Tailwind/class overrides, or internal selector targeting to alter their appearance. Use documented component props and variants instead.
- Owner-approved location-flag exception: native `<img>` elements may display locally bundled `flag-icons` rectangular SVG assets beside the existing location pin and location text. Use the field-icon width (1rem/16px), preserve the SVG aspect ratio with auto height, and prevent icon shrinking. The owner also approved subtle2px corners and identical native flag presentation beside country names in the filter modal. HeroUI Avatar cannot provide this size/shape through documented props. This permits only these native flag images and their native layout, not custom React components or HeroUI style overrides.
- Owner-approved salary-text exception: native `.salary-value` spans may use narrowly scoped color, weight, italic and underline styles for maximum-pay bands. This does not permit styling other UI or targeting HeroUI internals.
- A custom UI component is permitted only after confirming that HeroUI has no available component or documented composition that does something close to what is needed, explaining the gap, and obtaining explicit user approval. Lack of a suitable component is not permission by itself.
- Whenever an approved custom UI component is added, explicitly highlight it in the handoff, explain why HeroUI could not cover the need, and identify the files added or changed.

Start Pi from the repository root and trust the project when prompted. After configuration changes, restart Pi and verify the server is connected using `/mcp`.
