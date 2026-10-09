# supertale-fe

Standalone React SPA for SuperTale — the original creator frontend for the novel-to-video pipeline. Replaces the in-repo NiceGUI UI shipped from [`supertale-be`](https://github.com/claymorelab/SuperTale) and talks to its REST API exclusively.

This is the "traditional web UI" generation of SuperTale. The next-gen chat-driven pipeline lives in `superchat` + `dramaclaw`.

## Stack

- **Vite 6** + **React 19** + **TypeScript strict**
- **TanStack Router** — file-based routing, lazy child routes for feature-heavy pages
- **TanStack Query** + **ky** — server state, hierarchical query keys, SSE-driven invalidation
- **Zustand** (persisted) — auth + client state
- **React Hook Form** + **Zod** — forms and validation
- **shadcn/ui** (base-nova) + **Tailwind CSS 4** — design system
- **react-i18next** — zh / en, HTTP backend
- **Native EventSource** — long-running task progress streams
- **Vitest** + **MSW** — tests

## Prerequisites

- Node 20+
- Backend running locally: from the repo root `uv run novelvideo api --port 8780` (FastAPI on `:8780`)

## Quick start

```bash
npm install
cp .env.example .env        # default VITE_API_URL already points at :8780
npm run dev                 # Vite dev server on :5173 (proxies /api/v1 + /static to :8780)
```

Log in at <http://localhost:5173/login> with a backend-provisioned account.

## Commands

| Command                 | What it does                                                    |
| ----------------------- | --------------------------------------------------------------- |
| `npm run dev`           | Vite dev server on :5173 with `/api/v1` + `/static` proxy       |
| `npm run build`         | `tsc -b && vite build` — production bundle in `dist/`           |
| `npm run build:ce`      | Source-available CE production bundle with CE fallback env           |
| `npm run preview`       | Serve the production build locally                              |
| `npm test`              | Vitest single run                                               |
| `npm run test:watch`    | Vitest watch mode                                               |
| `npx tsc --noEmit`      | Type check only                                                 |

## Environment

```bash
VITE_API_URL=http://localhost:8780   # dev proxy + production API origin
```

Default and EE builds use the existing `build` command. Source-available CE release builds use
`build:ce`, which loads `.env.ce`.

At runtime, the app still prefers backend `GET /api/v1/config` as the source of truth.
`VITE_EDITION=ce` only decides the fallback edition when that request is unavailable.

Auth is cookie-backed. `AuthStore.login()` POSTs `/api/v1/auth/login` with `credentials: "include"`; the backend sets an HttpOnly `st_api_key` cookie, and the SPA persists only `{ username, role }` as a login marker. Every subsequent `ky` request uses `credentials: "include"` so the browser attaches the cookie. 401 on any request logs out + hard-redirects to `/login`.

## Route map

```
/login                                                    public
/_app                                                     auth guard (sidebar + header)
  /                                                       project dashboard
  /projects/$project/ingest                               upload novel, detect chapters
  /projects/$project/characters                           character CRUD, portraits, costumes
  /projects/$project/styles                               style lab (presets + custom)
  /projects/$project/tasks                                task monitor (filter + deep-links)
  /projects/$project/episodes                             episode list + master-detail shell
    /episodes/$episode/overview                           brief / summary / identities
    /episodes/$episode/script                             beat editing, rhythm toggle
    /episodes/$episode/sketches                           sketch pool picker
    /episodes/$episode/audio                              TTS + voice assignment
    /episodes/$episode/video                              per-beat video generation
    /episodes/$episode/compose                            final MP4 compose
```

All episode routes are code-split via `.lazy.tsx`.

## Async tasks (SSE)

Long-running operations (ingest, script, sketch, video, compose) follow this pattern:

1. Mutation returns `TaskResponse { ok, task_type, message }`
2. `useTaskStream` opens `EventSource` at `/api/v1/tasks/{type}/{project}/{episode}/stream?api_key=…`
3. Server pushes named events (`pending` / `starting` / `running` / `completed` / `failed`) with progress + current task + result
4. On terminal event: stream closes, target query keys invalidate, toast fires
5. Pages use `useStageTask` (shared wrapper) for reconcile-on-mount, real cancellation via `DELETE /tasks/{type}/{project}/{episode}`, and the shared `<StageProgressPanel>` component

## Testing

- Unit: `npm test` (auth store, media-url, api client) — Vitest + MSW
- E2E: ad-hoc via [`gstack browse`](https://github.com/simonren/skills) (`$B`), no automated framework yet
- **Cost guard**: smoke walks must never click Generate / Plan / Build / Analyze / Preview / Optimize / Regenerate — those trigger paid LLM / image-gen / TTS / video endpoints

## Deployment

Hosted on Cloudflare Workers across four tag-driven environments:

| Env | URL | Trigger |
| --- | --- | --- |
| dev     | <https://tale-dev.dramaclaw.ai>     | push to `main` |
| test    | <https://tale-test.dramaclaw.ai>    | push tag `vX.Y.Z` |
| preview | <https://tale-preview.dramaclaw.ai> | `gh workflow run deploy.yml -f action=preview -f version=vX.Y.Z` (shadow-prod, Zero Trust gated) |
| prod    | <https://tale.dramaclaw.ai>         | `gh workflow run deploy.yml -f action=promote-prod -f version=vX.Y.Z` (5% canary, manual ramp) |

Full runbook — secrets setup, canary ramp/rollback, Cloudflare Zero Trust config, version-stamping recipe, caveats — in [`DEPLOY.md`](./DEPLOY.md).

## Further reading

- [`CLAUDE.md`](./CLAUDE.md) — architectural deep-dive, auth flow, query key hierarchy
- [`DEPLOY.md`](./DEPLOY.md) — operator runbook for all four environments
- [`PROJECT_SPEC.md`](./PROJECT_SPEC.md) — full product spec
- [`DESIGN.md`](./DESIGN.md) — visual design system and component patterns
- [`docs/todo.md`](./docs/todo.md) — outstanding work, backend-blocked items, recently shipped

## Related repos

- [`claymorelab/SuperTale`](https://github.com/claymorelab/SuperTale) — `supertale-be`, FastAPI + NovelVideo pipeline
- `dramaclaw` / `superchat` — next-gen chat-driven creator pipeline (internal)

## Local multi-region dev

To exercise the `multi-region` cluster mode end-to-end locally:

1. **Run two backend instances** on different ports, e.g. `:8780` (cn-1) and `:8781` (us-1). Each must:
   - Set `SUPERTALE_CORS_ORIGINS=http://localhost:5173` (or emit `Access-Control-Allow-Origin` echo-from-allowlist + `Allow-Credentials: true`)
   - Emit `st_api_key` cookie with `SameSite=Lax` (same-origin behind the local gateway)

2. **Stand up a local edge dispatcher.** Simplest is Caddy:

   ```caddy
   tale.lingshan.localhost {
     @cn1 header Cookie *server-region=cn-1*
     @us1 header Cookie *server-region=us-1*
     handle @cn1 { reverse_proxy localhost:8780 }
     handle @us1 { reverse_proxy localhost:8781 }
     handle /api/* { respond 400 `{"ok":false,"error":"no_region"}` }
     handle /static/* { respond 400 `{"ok":false,"error":"no_region"}` }
     handle { reverse_proxy localhost:5173 }
   }
   ```

   Expose the regions manifest at the SPA origin (edge serves this statically — no cookie required):

   ```
   # /cluster-config.json
   { "regions": [ { "id": "cn-1", "displayName": "CN-1" }, { "id": "us-1", "displayName": "US-1" } ] }
   ```

3. **Configure the SPA.** Create `.env.local`:

   ```
   VITE_CLUSTER_MODE=multi-region
   VITE_CLUSTER_REGIONS_URL=/cluster-config.json
   ```

4. `pnpm dev` (or `npm run dev`) and visit `http://tale.lingshan.localhost`. You should see the region picker on `/login`.

### Smoke-test checklist

- [ ] Log in to cn-1, edit a beat, open the task panel.
- [ ] Click the region badge → pick us-1 → confirm. Page should hard-reload to `/login` with us-1 preselected.
- [ ] DevTools → Application → Local Storage: `st.episode.*`, `supertale-auth`, `supertale-seen-pools` gone; `supertale-app` and `i18nextLng` still present.
- [ ] DevTools → Application → Cookies: `server-region=us-1`; `st_api_key` issued by us-1 (not the stale cn-1 one).
- [ ] Open a second tab logged into us-1; go back to the first tab; trigger any action. First tab should show lockdown banner and hard-reload.
- [ ] Hit a stale `/api/*` with no `server-region` cookie: edge returns `400 no_region`; FE clears the region and redirects to `/login`.

## License

[Elastic License 2.0](../LICENSE) — Copyright (c) 2026 ClaymoreLab. Source available; see the root [LICENSE](../LICENSE) and [NOTICE](../NOTICE).
