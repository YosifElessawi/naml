# slice-1 — web server scaffold + static asset pipeline

## What changed
- New `web/` directory: Vite + React 18 + TypeScript (strict, `noUncheckedIndexedAccess`),
  plain CSS (no Tailwind/UI framework). pnpm is the package manager.
  Linter is biome (`pnpm --dir web lint`), tests are vitest+jsdom.
- New `naml/server.py`: aiohttp-based async server. Routes:
  - `GET /healthz` → `{"status":"ok"}`
  - `GET /api/state` → `project_state.build_hierarchy(cfg)` with weak
    SHA-256 ETag + 304 `If-None-Match` short-circuit (contract preserved
    from old stdlib server so `naml status` consumers keep working).
  - `GET /state` → `{}` placeholder; slice-11 swaps this for SSE.
  - `GET /` → serves `web/dist/index.html` when built, informative 404
    pointing at `pnpm --dir web build` otherwise.
  - `/assets/*` static mount, only registered if the dir exists.
- `naml/web.py` and `tests/test_web.py` deleted; `_cmd_serve` in `cli.py`
  now imports `naml.server`. Default port flipped 7777 → 8765 (the
  cockpit standard the spec/Vite proxy uses).
- `pyproject.toml`: `aiohttp>=3.9` added as the sole new runtime dep;
  sdist `exclude` now drops `web/node_modules`, `web/dist`, `web/.vite`.
- `.naml/config.toml`: `web-lint`, `web-typecheck`, `web-test` gates
  uncommented so downstream slices have them ready.
- Root `.gitignore` ignores `web/node_modules`, `web/dist`, `web/.vite`.

## Non-obvious decisions
- **No JS runtime deps committed yet.** `pnpm install` happens at sprint
  merge time / on the orchestrator host. The lockfile will land with the
  first slice that actually runs `pnpm install` (orchestrator step or
  slice-2). All gate commands are wired regardless.
- **`build_app(cfg, web_dist=...)` is the unit-test seam** — lets tests
  point `/` at a tmp dir. `serve()` resolves dist from `cfg.repo_root`.
- **Vite dev proxies `/api`, `/healthz`, `/state` to :8765** so the
  browser only ever talks to one origin in both dev and prod.

## Notes for downstream slices
- The data wire is aiohttp on **port 8765**; cockpit fetches `/api/state`.
- SSE: add a new aiohttp route in `naml/server.py` (slice-11); the
  `/state` placeholder is the slot.
- Frontend lives at `web/src`; co-locate `*.test.tsx` with components.
- New runtime Python deps still need to land in `pyproject.toml` (and
  the slice spec only OKs `aiohttp` + `watchdog`).
