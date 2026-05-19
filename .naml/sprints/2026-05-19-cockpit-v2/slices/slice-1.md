# slice-1: Web server scaffold + static asset pipeline

## What to build

Stand up the foundation that every other slice builds on:

1. A `web/` directory at the repo root with a TypeScript + Vite project
   (React + plain CSS, no UI framework). Configure `pnpm` as the package
   manager. Lint with biome or eslint (pick one and commit).
2. A new naml CLI subcommand: `naml serve [--port 8765]` that starts an
   aiohttp-based async HTTP server. The server's job is to:
   - Serve the built `web/dist/` directory at `/`
   - Expose a `/healthz` endpoint returning `{"status": "ok"}`
   - Expose a placeholder `/state` route returning `{}` (real SSE comes in slice-11)
3. **Remove the existing stdlib-based server.** Naml has a Phase-5
   stopgap `naml/web.py` (stdlib `http.server`) and a corresponding
   `serve` subcommand registered in `naml/cli.py` — both shipped to give
   the dashboard work an early `/api/state` endpoint. They are being
   superseded by your aiohttp `naml/server.py`. **Delete `naml/web.py`,
   delete `tests/test_web.py`, and remove the `_cmd_serve` function +
   its parser registration from `naml/cli.py`** so there's exactly one
   `naml serve` implementation after this slice merges. The new aiohttp
   server should preserve the existing `/api/state` ETag contract
   (response body is `naml.project_state.build_hierarchy(cfg)`, weak
   ETag = SHA-256(body)[:16]) so Phase 5's `naml status` CLI and any
   external consumers don't break.
4. Wire `pnpm dev` to Vite's dev server and document the dev workflow in
   `web/README.md` (run `naml serve` for the Python side, `pnpm --dir web dev`
   for hot-reload during development).
5. Update `.naml/config.toml` gates: uncomment the `web-lint`, `web-typecheck`,
   `web-test` gates so subsequent slices have their gates ready.
6. Add the new web tier to `pyproject.toml`'s sdist exclude list (don't ship
   `node_modules` or `web/dist`).

## Acceptance criteria

- [ ] `naml serve` starts a server on the configured port without error
- [ ] `curl localhost:<port>/healthz` returns `{"status":"ok"}`
- [ ] `curl localhost:<port>/api/state` returns the same JSON shape that
      the old stdlib server returned (project + current_sprint + lanes),
      with a weak ETag header and 304 on `If-None-Match` match
- [ ] `pnpm --dir web build` produces `web/dist/index.html`
- [ ] `pnpm --dir web typecheck` passes on the starter project
- [ ] `pnpm --dir web lint` passes
- [ ] `pnpm --dir web test --run` passes (one smoke test)
- [ ] `naml serve` serves the built `index.html` at `/`
- [ ] `naml/web.py` and `tests/test_web.py` are deleted
- [ ] Exactly one `naml serve` subcommand is registered in `naml/cli.py`
- [ ] Existing Python tests still pass (no regression in `pytest tests/`)

## Artifacts (referenced, not embedded)

- ../artifacts/spec.md
- ../overview.md

## Constraints

- **No UI framework** — plain React + CSS modules. Tailwind is out.
- **Server is async** (aiohttp or asyncio HTTP) — slice-11 needs to hold
  long-lived SSE connections; a sync WSGI server will be in your way.
- **No new runtime Python deps beyond aiohttp + watchdog** (watchdog lands
  in slice-10, aiohttp lands here). Keep naml as close to stdlib as possible.
- **Bind to 127.0.0.1 only.** Localhost-only. No external listeners.

## Notes from grilling session

This is the unblocker for every other UI slice. Get the dev-server hot-reload
loop working before you start adding views — that loop is what makes the
remaining 13 slices fast to implement. If Vite + aiohttp don't compose
cleanly, document the workaround in `web/README.md` rather than fighting it.

The split is: Vite dev server during development (port :5173, talks to
`naml serve` on :8765 via dev-proxy), aiohttp serves the built bundle in
production. The browser only ever talks to one host.
