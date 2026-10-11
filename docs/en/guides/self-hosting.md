<!-- lang-switch -->
**English** · [简体中文](../../zh/guides/self-hosting.md)

# Self-Hosting Handbook (Docker)

> Deploy, configure, upgrade, and back up DramaClaw CE with Docker.

CE ships three containers: `api` + `newapi` (LocalNewAPI, idle until you switch to Custom or LocalNewAPI + Official Hybrid mode) + `web`, with **no PostgreSQL / no Redis / no Celery** (`ST_EDITION=ce`; tasks run inline within the process). Models go through the official DramaClaw gateway by default.

Two compose files ship in the repo, and **both build everything from source — no published images are ever pulled**. `docker-compose.release.yml` is the single source of the runtime definition for all three services (env / ports / volumes / healthchecks) and also carries their `build:` blocks; `docker-compose.yml` (the default entry point) simply `extends` it. The DramaClaw gateway is vendored under `third_party/dramaclaw-gateway` (AGPL-3.0), so you do **not** clone it separately. Build with `docker compose up -d --build`.

## 1. Prerequisites

- Docker + `docker compose`.
- Docker Compose ≥ 2.24 (`docker compose version`).
- Resources: ≥ 2 vCPU / 4GB recommended (excluding model inference, which runs through an external gateway).
- A DC key (the default official gateway, see <https://susciyuan.com>), or your own OpenAI-compatible gateway.

## 2. Get the compose file and configuration

```bash
git clone https://github.com/liujh0729-jpg/aipdd-drama-agent.git
cd aipdd-drama-agent   # the gateway is already vendored under third_party/dramaclaw-gateway — no separate clone
cp .env.example .env
```

Both files build all three services from source; no changes needed:

| File | Role | Command |
|---|---|---|
| `docker-compose.yml` | Default entry point — `extends` the file below and adds local build tags | `docker compose up -d --build` |
| `docker-compose.release.yml` | Runtime-definition base — also builds `api`/gateway/`web` from source | `docker compose -f docker-compose.release.yml up -d --build` |

Key points shared by both (defined once in `docker-compose.release.yml`, reused by `docker-compose.yml` via `extends`):

| Item | Value | Notes |
|---|---|---|
| Services | `api` + `newapi` + `web` | No PG/Redis; `newapi` is LocalNewAPI, built from the vendored `third_party/dramaclaw-gateway` |
| Build context | `api` = `.` · `newapi` = `${DRAMACLAW_GATEWAY_SRC:-./third_party/dramaclaw-gateway}` · `web` = `./frontend` | Local build tags `aipdd/api`, `aipdd/gateway`, `aipdd/web`; zero external images |
| Gateway source | `third_party/dramaclaw-gateway` (vendored) | To build from your own checkout instead, set `DRAMACLAW_GATEWAY_SRC=<path>` in `.env` |
| Port | `8780:8780` | REST API |
| Gateway admin port | `${ST_NEWAPI_BIND:-127.0.0.1}:${ST_NEWAPI_PORT:-3000}:3000` | Bound to `127.0.0.1` by default; set `ST_NEWAPI_BIND=0.0.0.0` in `.env` to widen it. **[aipdd fork] `docker-compose.aipdd.yml` no longer maps this port**: the gateway is an internal transport only, reachable externally via the web port's `/gateway/v1/` (that is `NEWAPI_PUBLIC_BASE_URL`, used by the model plaza call examples) |
| Enforced environment | `ST_EDITION=ce`, control-plane/Redis/Celery cleared | CE mode cannot be downgraded |
| Data volume | `ce-data:/data` (output at `/data/output`) | Persists project databases, settings, and generated media |

## 3. Configure `.env`

> ⚠️ **Secret-type defaults (such as `PROMPT_EXPORT_PASSWORD=change_me`) must be changed.** For the model gateway, see [Model Configuration](#model-configuration).

Groups (each item is commented inline in `.env.example`): LocalNewAPI provisioner, reference-media OSS relay (OSS_RELAY_*), Cognee knowledge graph, text/image/video/audio models, image and video base parameters, UI, and output directories. Channel selection, gateway address, and token are saved from the web UI to `settings.db`.

### Model Configuration

Recommended and alternative options (see [Configuring Model Providers](../getting-started/configuring-models.md) for details):

- **A. DC official key (recommended)**: the default compose already uses the official gateway. After bringing the stack up, open `http://localhost:8080` → Settings → Models & Channels → Official → paste your DC key and save to start using it, **no model mapping required**. Get a key at <https://susciyuan.com>.
- **B. LocalNewAPI**: LocalNewAPI is already running; open Settings → Models & Channels → Custom, click Initialize, then configure upstream channels and model mappings from the LocalNewAPI page.

LocalNewAPI must map DramaClaw's logical models to real upstream models. The reference-image feature needs `OSS_RELAY_AK/SK` (you can skip it for a text-only workflow).

## 4. Start / Stop

```bash
docker compose up -d --build                              # source build: start (builds on first run)
docker compose ps                                          # status
docker compose logs -f api                                 # logs
docker compose down                                        # stop (keeps the data volume)
```

## 5. Where the data lives / Backup, restore & migrate

- Project databases, settings, and generated media live in the named volume `ce-data` (`/data` inside the container); generated media is written to `/data/output`. Removing or rebuilding a container keeps this volume. Only an explicit `docker compose down -v` removes it.
- Back up the data volume:

```bash
docker run --rm -v dramaclaw-ce_ce-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/ce-data-backup.tar.gz -C /data .
```

(The volume name is prefixed with the compose project name; run `docker volume ls` to confirm the actual name.)

- Restore, or move to a new machine — copy `ce-data-backup.tar.gz` to the target host, then unpack it back into the data volume (the `-v` mount creates the volume if it does not exist yet):

```bash
docker run --rm -v dramaclaw-ce_ce-data:/data -v "$PWD":/backup alpine \
  tar xzf /backup/ce-data-backup.tar.gz -C /data
```

Then bring the stack up as usual (`docker compose up -d`). The volume backup already includes generated media; back up `.env` separately.

- The `newapi-data` volume holds LocalNewAPI's SQLite database (upstream channels, keys, tokens) and is not regenerable — back it up the same way:

```bash
docker run --rm -v dramaclaw-ce_newapi-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/newapi-data.tgz -C /data .
```

Restore it the same way, into the target volume:

```bash
docker run --rm -v dramaclaw-ce_newapi-data:/data -v "$PWD":/backup alpine \
  tar xzf /backup/newapi-data.tgz -C /data
```

## 6. Upgrades

Source build:

```bash
git pull
docker compose up -d --build
```

`docker compose up -d --build` also rebuilds the gateway from the vendored `third_party/dramaclaw-gateway` (Go + bun, several minutes). When only this repo's code changed, rebuild just the two local services: `docker compose up -d --build api web`; when only the vendored gateway changed, `docker compose up -d --build newapi`.

Your `.env` is never touched by the upgrade. The `ce-data` and `newapi-data` volumes are reused.

If an older release wrote media to `/app/output` inside the container, run the one-time migration **before** starting the new version (zip users: download `scripts/migrate_docker_output.py` from GitHub instead of `git pull`):

```bash
git pull
docker compose exec -T api python - < scripts/migrate_docker_output.py
docker compose up -d
```

On Windows PowerShell:

```powershell
git pull
Get-Content scripts/migrate_docker_output.py -Raw | docker compose exec -T api python -
docker compose up -d
```

The script copies only missing files, never overwrites or deletes the source, and backs up `projects.db` before updating project paths. Files that existed only in an already-removed container layer cannot be recovered from the data volume.

### Upgrading from a previous checkout

`docker-compose.selfhosted.yml` and `docker-compose.selfhosted.release.yml` have been removed. Use the two files above instead — service names and the `ce-data` / `newapi-data` volumes are unchanged, so existing data carries over as-is.

| Before | Now |
|---|---|
| `docker compose up -d --build` (source build with a `../dramaclaw-gateway` sibling clone) | Same command; the gateway now comes from the vendored `third_party/dramaclaw-gateway`, so no sibling clone is needed |
| `docker compose -f docker-compose.release.yml up -d` (image mode) | Image mode is retired — `docker-compose.release.yml` now builds from source too; add `--build` |
| `docker compose -f docker-compose.selfhosted.yml ...` / `docker-compose.selfhosted.release.yml ...` | Use `docker-compose.yml` / `docker-compose.release.yml` (both source build); the `newapi-data` volume is reused — back it up first |
| `.env`'s `NEWAPI_BASE_URL` / `NEWAPI_API_KEY` / `ST_*_PORT` / `INSTALL_WORLD` / `NEWAPI_PROVISIONER_ENABLED` | Unchanged, still effective |

## 7. Troubleshooting

| Symptom | What to check |
|---|---|
| Container won't start | `docker compose logs api`; usually the `.env` gateway address/key was not changed or is unreachable |
| Port 8780 already in use | Change the left-hand value of `ports` in compose, e.g. `8888:8780` |
| Port 3000 already in use (LocalNewAPI fails to start) | **[aipdd fork] `docker-compose.aipdd.yml` does not map 3000**, so this only applies to the two upstream compose files. There the port is bound to `127.0.0.1` by default; set `ST_NEWAPI_PORT=<free port>` in `.env` and restart. `api` never waits on the gateway's health, so it is not blocked. |
| Model call errors | Confirm the gateway is reachable and that the `*_MODEL` names exist in the gateway backend |

## Related

- [Quickstart](../getting-started/quickstart.md) ｜ [Configuring Model Providers](../getting-started/configuring-models.md)
