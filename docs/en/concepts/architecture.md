<!-- lang-switch -->
**English** · [简体中文](../../zh/concepts/architecture.md)

# Architecture

DramaClaw Community Edition (CE) is a **single-machine** "novel → finished video" pipeline: one FastAPI service hosts every creative capability, tasks run in-process, data lands locally, and models are reached through an OpenAI-compatible gateway. **No PostgreSQL / Redis required.**

## System Architecture

<p align="center">
  <img src="../../../assets/architecture.png" alt="DramaClaw CE system architecture — browser, FastAPI engine, local storage, model gateway" width="900"/>
</p>

## Processing Flow

<p align="center">
  <img src="../../../assets/pipeline.png" alt="Processing flow — ingest, plan, produce, deliver" width="900"/>
</p>

Every stage has its own interface, so you can run them in sequence, skip steps, or resume from any checkpoint.

## Code Structure

Main subpackages of `src/novelvideo/`:

| Package | Responsibility |
|---|---|
| `api` | Project-scoped REST routes |
| `agents` | Script / asset / identity / planning and other agents |
| `generators` | Image / video / audio generation adapters |
| `audio` | Voiceover (TTS) synthesis |
| `cognee` | Story knowledge graph (parses the novel, builds characters / relationships / timeline) |
| `freezone` | Free-form creative canvas |
| `director_world` | 3DGS / world scene features |
| `chat` | Conversational creative assistant |
| `export` | Episode composition and finished-video export |
| `verification` | Validation and review tooling |
| `task_backend` | Task scheduling and runner (in-process execution in CE) |
| `storage` / `sqlite_store` | Local storage |
| `ports` | Ports and adapters layer (see below) |
| `prompts` / `styles` | Baseline prompts and styles |

## Ports and Adapters (one codebase, two distributions)

DramaClaw uses **Ports & Adapters (hexagonal architecture)** to decouple the "engine" from its "runtime environment." `src/novelvideo/ports/` defines a set of Protocol interfaces, each with a **CE default implementation** (single-machine / local); the commercial Enterprise Edition (EE) provides enterprise adapters (multi-tenant / distributed) for the **same set of interfaces**.

The hard rule: **core code never imports enterprise code**; at runtime the matching implementation is injected based on `ST_EDITION`. The CE default implementations ship with the core and work out of the box.

| Port | CE default | Enterprise adapter |
|---|---|---|
| Auth | Local single user, no login | Multi-tenant auth |
| ProjectRegistry | Local SQLite index | Fleet registry / routing |
| ProjectAccess | owner-everywhere | Tenant RBAC |
| TaskBackend | In-process inline | Celery distributed |
| CancellationStore | In-process | Redis |
| UsageMeter | no-op | Metering and billing |
| Lifecycle | no-op | PG pool / Redis |
| AuditSink | Local NDJSON | Audit tables |
| CreditQuote | cost = 0 | Price table |

> The port definitions, the registry, and the CE default implementations all live on the core side; EE only supplies implementations plus registration at startup. This is what makes "one codebase, no fork between community and commercial" possible.

## Task Execution

CE's TaskBackend = **in-process inline** (background thread + EventSource progress stream): resumable, cancelable, but **no Redis / Celery needed**. The progress / cancellation experience in the task center matches the Enterprise Edition.

## Data and Storage

CE is fixed to a **single local user**, and all project data stays local:

- `state/<user>/<project>/data.db` (SQLite) — structured project data
- `output/<user>/<project>/...` — finished videos and assets

No external database is required.

## Model Access

All text / image / video / audio models are reached through a single **OpenAI-compatible gateway** (LocalNewAPI or an official key); see [Configuring Model Providers](../getting-started/configuring-models.md) for details.

## Tech Stack

Python + FastAPI (REST API, `:8780`) · CLI `novelvideo` · React frontend · Docker Compose self-hosting.
