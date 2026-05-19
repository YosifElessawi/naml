# naml cockpit — web UI

The V2 cockpit single-page app. Built with Vite + React + TypeScript and plain
CSS modules. No UI framework, no Tailwind.

## Dev workflow

Two processes during development:

```bash
# terminal 1 — the Python side (aiohttp, serves /api, /healthz, /state)
naml serve --port 8765

# terminal 2 — Vite dev server with hot reload (proxies /api → :8765)
pnpm --dir web dev
```

Open <http://127.0.0.1:5173>. The browser only ever talks to Vite during dev;
Vite proxies `/api/*`, `/healthz`, and `/state` to the aiohttp server on
:8765, so SSE and ETag caching behave identically to production.

## Production

```bash
pnpm --dir web build           # writes web/dist/
naml serve --port 8765         # aiohttp serves web/dist/ at /
```

The browser hits a single origin in production — no proxy involved.

## Quality gates

These map 1:1 to the gates in `.naml/config.toml`:

```bash
pnpm --dir web lint            # biome
pnpm --dir web typecheck       # tsc --noEmit
pnpm --dir web test --run      # vitest (jsdom)
pnpm --dir web build           # production bundle
```

## Conventions

- Plain CSS (no Tailwind). Co-locate component styles or use a shared file in
  `src/`.
- Strict TypeScript (`strict`, `noUncheckedIndexedAccess`). Fix the type, don't
  cast around it.
- Tests live next to source as `*.test.tsx` and use Vitest + React Testing
  Library against jsdom.
