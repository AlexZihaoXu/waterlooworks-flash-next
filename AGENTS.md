# Project instructions

Follow `README.md` and `CONTRIBUTING.md`, including TDD, Conventional Commits, and the no-mock-data policy. Keep `.experiment/` ignored and private data out of Git.

## Credentials

The root `.credentials.json` is user-managed and must stay gitignored, excluded from Docker build contexts, and excluded from formatting tools. Agents must never view, read through tools, search, print, or otherwise expose its contents. Exclude it from broad repository searches and diagnostic collection.

Only application or authentication code may load it dynamically at runtime when credentials are needed, using the `username` and `password` keys. Never hardcode, copy, log, return, or include these values in tool output, exceptions, screenshots, traces, or other artifacts. Validation errors must not include credential values. Do not overwrite the file or modify user-provided credentials. Future container access must use an explicit runtime secret or read-only mount, never a Docker image layer.

## Pi plugins

Do not install Pi plugins or add them to project settings for automatic installation. If a plugin is needed, explain why and ask the user to install it themselves. This applies to both global and project-local installations.

## HeroUI v3

The official HeroUI React MCP server is configured project-locally in `.mcp.json`. The MCP adapter must be supplied by the user's Pi setup; this repository must not install it. `.pi/mcp.json` enables startup connection, keep-alive, and direct tools for `heroui-react` when the adapter is available.

Before implementing or changing HeroUI UI, consult the official MCP tools for the relevant v3 documentation. Do not assume v2 APIs. If the server is unavailable, report the connection problem rather than claiming its documentation was checked. Keep this configuration project-scoped; do not move it into global settings.

### UI component rules

- Use HeroUI components rather than inventing custom UI components. Never create a custom UI component without explicit user permission.
- Before using a specific component, check HeroUI's official v3 component list, reference designs/examples, and that component's usage documentation. Use the official MCP tools; do not rely on memory or v2 APIs.
- Never make CSS-level modifications to HeroUI components: no custom CSS overrides, inline styles, Tailwind/class overrides, or internal selector targeting to alter their appearance. Use documented component props and variants instead.
- A custom UI component is permitted only after confirming that HeroUI has no available component or documented composition that does something close to what is needed, explaining the gap, and obtaining explicit user approval. Lack of a suitable component is not permission by itself.
- Whenever an approved custom UI component is added, explicitly highlight it in the handoff, explain why HeroUI could not cover the need, and identify the files added or changed.

Start Pi from the repository root and trust the project when prompted. After configuration changes, restart Pi and verify the server is connected using `/mcp`.
